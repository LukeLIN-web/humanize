"""goal_reviewed -- a goal worked by one agent, read by a fresh reviewer, a bounded number of times.

    hmz exec -f goal_reviewed -a actor=claude/claude-fable-5-2:high \
        -a reviewer=codex/gpt-5.6-sol:high -p budget.duration=12h "the goal"

The actor takes the task as its CLI's own goal, so it keeps taking turns (waiting on jobs,
dispatching subagents) until it judges the goal met. A reviewer in a new session then reads the result against the task and the project's own rules. Its review
is written to `.hmz/reviews/<run>/review-<n>.md` and becomes the actor's next goal.

Unlike rlar, the reviews are bounded: after `max_reviews` of them the actor applies the last
one and the run ends without another reading. The last reviewer is told so, and asked for
fixes the actor can apply as written.

A choice only the user can make goes to the user, not back to the actor: the flow asks it and
waits, spending nothing, and the answer goes into the actor's next goal. With nobody there (`hmz
exec`, or the person marked away) it is written to `for-user.md` beside the reviews instead, and
the actor is told to leave it.
"""

import time

from hmz.flows import (
    Agent,
    AgentCollection,
    EnvCollection,
    FilesEnvMixin,
    FlowContext,
    FlowParams,
    GoalCommandAgentMixin,
    LocalEnv,
    Markdown,
    Outworlder,
    Permission,
    flow,
)
from pydantic import BaseModel, Field


class Actor(Agent, GoalCommandAgentMixin):
    """Does the work, anywhere on the machine a goal sends it: outputs, caches, other repos."""

    _permission = Permission("danger-full-access")


class Reviewer(Agent):
    """Reads and runs checks. Told to write nothing, not fenced from the workdir: a Codex
    role granted no write cannot start its own sandbox inside hmz's and runs no command."""


class Agents(AgentCollection):
    actor: Actor
    reviewer: Reviewer
    user: Outworlder


class Workspace(LocalEnv, FilesEnvMixin):
    """Where both work; the flow writes each review into it."""


class Envs(EnvCollection):
    workspace: Workspace


class Params(FlowParams):
    max_reviews: int = Field(
        default=2, ge=0, le=5, description="reviews before the run ends on the actor's fix"
    )


class Review(BaseModel):
    """The descriptions are the instruction: they are the shape the reviewer answers in."""

    model_config = {"extra": "forbid"}

    done: bool = Field(
        description="True only if the goal is completely and correctly met and no finding "
        "below needs action. False if anything is left to do or to fix."
    )
    notes: str = Field(
        description="The review, written as a message to the agent that did the work. It is "
        "passed on word for word and is all that agent will hear from you. One numbered "
        "finding per problem: where it is (file and line, artifact path, issue or PR), what "
        "is wrong, the evidence, and what to do. When done is true: what was delivered and "
        "how you checked it."
    )
    for_user: list[str] = Field(
        default=[],
        description="Choices only the user can make: the goal and the decisions recorded in the "
        "project leave them open, and no evidence in the repository settles them. One entry "
        "each: the question, the options, and which you recommend and why. They go to the "
        "user, not to the agent; do not list them in notes as well. Empty when there are none.",
    )


REVIEW = """You are reviewing work another agent just finished, in its working directory. \
You have no part in that work and may write nothing; read and run checks only.

Hold the work to two things:
1. The goal below, and any acceptance criteria it points to (an issue, a plan, a checklist).
2. The project's own rules: read CLAUDE.md, the CLAUDE.md of each directory the work touched, \
and CONTRIBUTING.md if there is one. Decisions recorded there are settled; do not reopen them.

What the agent said is a claim; the repository and the artifacts are the evidence. Check that \
every number written into a document or table matches the result file it came from, that every \
conclusion claims no more than its evidence supports, and that nothing was stubbed, faked or \
special-cased to look finished. Use git status, git diff and git log to see what changed. Run \
cheap checks yourself (tests, linters, recomputing a statistic from a result file); do not \
launch training, evaluation or anything that needs a GPU -- read their logs and outputs instead.

Report only what the agent must act on. Do not ask for more experiments than the goal asked for.
{last}
Goal:
{task}

What the agent said as it ended:
<agent_said>
{said}
</agent_said>
"""

LAST = """
This is the last review of this run: the agent will apply it and the run ends with no further \
reading. For every finding give the fix itself -- the replacement sentence, the corrected \
number, the exact command -- so that it can be applied as written.
"""

# The condition must be one the actor can meet when a finding is the user's to settle: with "and
# the earlier goal still holds" in it, a goal whose own requirements conflict sent the actor back
# on every stop, saying the same thing each time.
FIX = (
    "/goal For every finding in the review at {path}, one of these is true: it is fixed; it is "
    "answered with evidence where the review is wrong; or your final message names it as "
    "needing the user's decision and says what the choices are. Nothing the work already "
    "achieved is undone. Read that file first."
)
DECIDED = " The user answered the questions under 'For the user' there; carry out that answer."
LEFT = " The questions under 'For the user' there are the user's: leave them as they are."

ASK = "The reviewer found choices only you can make. Answer each by its number."


def numbered(items: list[str]) -> str:
    return "\n".join(f"{i}. {item}" for i, item in enumerate(items, 1))


@flow(agents=Agents, envs=Envs, params=Params)
async def goal_reviewed(
    task: str, *, agents: Agents, envs: Envs, params: Params, ctx: FlowContext
) -> str:
    """One agent works a goal; a fresh read-only reviewer sends it back at most max_reviews times."""
    actor, reviewer, user = agents["actor"], agents["reviewer"], agents["user"]
    workspace = envs["workspace"]
    run = time.strftime("%Y%m%dT%H%M%S")
    working = await actor.spawn()
    asking = await user.spawn()
    said = await actor.run(f"/goal {task}", session=working, env=workspace)
    for n in range(1, params.max_reviews + 1):
        review = await reviewer.run(
            REVIEW.format(
                task=task, said=said, last=LAST if n == params.max_reviews else ""
            ),
            session=await reviewer.spawn(),
            env=workspace,
            output_schema=Review,
        )
        path = f".hmz/reviews/{run}/review-{n}.md"
        text, answer = review.notes, ""
        if review.for_user:
            text += f"\n\n## For the user\n\n{numbered(review.for_user)}\n"
            answer = await user.run(
                ASK,
                session=asking,
                context=(
                    Markdown(title="Questions", text=numbered(review.for_user)),
                    Markdown(title="The review", text=review.notes),
                ),
            )
            if answer:
                text += f"\n## The user's answer\n\n{answer}\n"
            else:
                await workspace.write(f".hmz/reviews/{run}/for-user.md", text.encode())
        await workspace.write(path, text.encode())
        if review.done and not answer:
            return text
        goal = FIX.format(path=path) + (DECIDED if answer else LEFT if review.for_user else "")
        said = await actor.run(goal, session=working, env=workspace)
    return said
