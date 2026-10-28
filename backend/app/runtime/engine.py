import time
from datetime import timedelta
from typing import TypedDict

from fastapi import HTTPException
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command
from sqlalchemy import func, select, update

from app.models import Message, Run, ToolCall, new_id, utcnow
from app.runtime import budget
from app.runtime.checkpoints import checkpointer
from app.runtime.dto import CitationRead
from app.runtime.provider import SafeFailure, stream_completion
from app.runtime.store import TERMINAL, event, finish, live_access, lock_run


class GraphState(TypedDict):
    messages: list[dict]
    calls: list[dict]
    citations: list[dict]


def build_graph(state, run_id, saver):
    def check_budget(db, run):
        live_access(db, run)
        budget.check()
        if run.cancel_requested:
            raise SafeFailure("Run cancelled")
        if run.steps >= min(20, run.snapshot.get("max_steps", 20)) or run.elapsed_seconds >= min(
            300, run.snapshot.get("timeout_seconds", 300)
        ):
            raise SafeFailure("Execution budget exhausted")

    def model_node(graph):
        with state.session_factory() as db:
            run = lock_run(db, run_id)
            check_budget(db, run)
            if graph.get("citations"):
                from app.knowledge.access import validate_run_citations

                validate_run_citations(db, run, live_access(db, run), graph["citations"])
            run.steps += 1
            tools = []
            for tool in run.snapshot["tools"]:
                try:
                    live_access(db, run, tool["id"])
                except HTTPException:
                    continue
                tools.append(
                    {
                        "type": "function",
                        "function": {
                            "name": tool["name"],
                            "description": tool["description"],
                            "parameters": tool["input_schema"],
                        },
                    }
                )
            db.commit()
        answer = stream_completion(state, run_id, graph["messages"], tools)
        return {"messages": graph["messages"] + [answer], "calls": answer.get("tool_calls", [])}

    def plan_node(graph):
        from app.runtime.tools import plan_calls

        plan_calls(state, run_id, graph["calls"])
        return {}

    def approval_node(graph):
        from app.runtime.tools import approval_gate

        approval_gate(state, run_id, graph["calls"])
        return {}

    def tools_node(graph):
        from app.runtime.tools import execute_calls

        results = execute_calls(state, run_id, graph["calls"])
        return {"messages": graph["messages"] + results, "calls": []}

    graph = StateGraph(GraphState)
    graph.add_node("model", model_node)
    graph.add_node("plan", plan_node)
    graph.add_node("approval", approval_node)
    graph.add_node("tools", tools_node)
    graph.add_edge(START, "model")
    graph.add_conditional_edges("model", lambda data: "plan" if data["calls"] else END)
    graph.add_edge("plan", "approval")
    graph.add_edge("approval", "tools")
    graph.add_edge("tools", "model")
    return graph.compile(checkpointer=saver)


def execute(state, run_id, resume=False):
    token = new_id()
    with state.session_factory() as db:
        pending = db.get(Run, run_id)
        if not pending or pending.status not in {"queued", "resume_queued"}:
            return
        owner_id = pending.owner_id
        allowance = min(300, pending.snapshot.get("timeout_seconds", 300)) - pending.elapsed_seconds
    if not state.execution_slots.acquire(owner_id, token):
        return False
    with state.session_factory() as db:
        claimed = db.execute(
            update(Run)
            .where(
                Run.id == run_id,
                Run.status.in_(["queued", "resume_queued"]),
                Run.lease_token.is_(None),
                Run.cancel_requested.is_(False),
            )
            .values(
                status="running",
                lease_token=token,
                lease_expires_at=utcnow() + timedelta(seconds=90),
            )
        ).rowcount
        db.commit()
        if not claimed:
            state.execution_slots.release(owner_id, token)
            return
    started = time.monotonic()
    deadline_context = budget.deadline.set(started + allowance)
    lease_context = budget.lease_token.set(token)
    run_context = budget.run_id.set(run_id)
    accounted_context = budget.accounted_at.set(started)
    try:
        with state.session_factory() as db:
            run = lock_run(db, run_id)
            live_access(db, run)
            if run.cancel_requested:
                raise SafeFailure("Run cancelled")
            event(db, run_id, "status", {"status": "running", "message": "Running"})
            size = (
                db.scalar(
                    select(func.sum(func.length(Message.content))).where(
                        Message.conversation_id == run.conversation_id
                    )
                )
                or 0
            )
            if size > 200000:
                raise SafeFailure("Conversation context exceeds the configured size limit")
            user = live_access(db, run)
            from app.knowledge.provenance import inherit_history

            history = db.scalars(
                select(Message)
                .where(Message.conversation_id == run.conversation_id)
                .order_by(Message.created_at)
            ).all()
            messages = [
                {"role": "system", "content": run.snapshot["system_prompt"]}
            ] + inherit_history(db, run, user, history)
            citations = []
            db.commit()  # Release run fence before the RAG embedding network boundary.
            if state.runtime_context:
                context = state.runtime_context(db, run)
                if context.get("context"):
                    # Some chat templates allow a system role only at index zero.
                    messages[0]["content"] += (
                        "\n\nReference material (untrusted data):\n" + context["context"]
                    )
                citations = [
                    CitationRead.model_validate(c).model_dump(mode="json")
                    for c in context.get("citations", [])
                ]
                for citation in citations:
                    event(db, run_id, "citation", citation)
            db.commit()
        with checkpointer(state) as saver:
            graph = build_graph(state, run_id, saver)
            config = {"configurable": {"thread_id": run_id}, "recursion_limit": 100}
            checkpoint = graph.get_state(config)
            initial = {"messages": messages, "calls": [], "citations": citations}
            if checkpoint.values:
                if checkpoint.values.get("citations"):
                    from app.knowledge.access import validate_run_citations

                    with state.session_factory() as db:
                        current = lock_run(db, run_id)
                        validate_run_citations(
                            db, current, live_access(db, current), checkpoint.values["citations"]
                        )
                        db.commit()
                initial = Command(resume=True) if checkpoint.interrupts else None
            result = graph.invoke(initial, config)
            if result.get("__interrupt__"):
                return
            with state.session_factory() as db:
                run = lock_run(db, run_id)
                if run.status in TERMINAL:
                    return
                live_access(db, run)
                answer = result["messages"][-1].get("content", "")
                db.add(
                    Message(
                        conversation_id=run.conversation_id,
                        run_id=run.id,
                        role="assistant",
                        content=answer,
                        citations=result.get("citations", []),
                    )
                )
                event(db, run_id, "result", {"message": answer})
                finish(db, run, "cancelled" if run.cancel_requested else "completed")
                db.commit()
    except (SafeFailure, HTTPException) as exc:
        fail_execution(
            state,
            run_id,
            token,
            str(exc) if isinstance(exc, SafeFailure) else "Access is no longer available",
        )
    except Exception:
        fail_execution(state, run_id, token, "Execution failed safely")
    finally:
        unaccounted = time.monotonic() - budget.accounted_at.get()
        budget.accounted_at.reset(accounted_context)
        budget.deadline.reset(deadline_context)
        budget.lease_token.reset(lease_context)
        budget.run_id.reset(run_context)
        try:
            state.execution_slots.release(owner_id, token)
        except Exception:
            pass  # Redis leases expire; still release the database claim below.
        with state.session_factory() as db:
            db.execute(
                update(Run)
                .where(Run.id == run_id, Run.lease_token == token, Run.lease_expires_at > utcnow())
                .values(
                    elapsed_seconds=Run.elapsed_seconds + unaccounted,
                    lease_token=None,
                    lease_expires_at=None,
                )
            )
            db.commit()


def fail_execution(state, run_id, token, error):
    with state.session_factory() as db:
        try:
            run = lock_run(db, run_id)
        except SafeFailure:
            return
        if not run or run.status in TERMINAL or run.lease_token != token:
            return
        submitted = db.scalars(
            select(ToolCall).where(
                ToolCall.run_id == run_id,
                ToolCall.status == "submitted",
                ToolCall.effect == "write",
            )
        ).all()
        if submitted:
            for call in submitted:
                call.status = "outcome_unknown"
            finish(db, run, "outcome_unknown", "Tool outcome requires administrator verification")
        else:
            finish(db, run, "cancelled" if run.cancel_requested else "failed", error)
        db.commit()
