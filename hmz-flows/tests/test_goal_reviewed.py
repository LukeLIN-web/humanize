"""goal_reviewed with scripted agents and person (hmz.sdk.fakes, hmz >= 0.3.0): no model is called.

    cd hmz-flows && python -m pytest -q tests
"""

import asyncio
import os

import pytest

from hmz.flows import CapabilityMissing
from hmz.sdk import fakes

FLOW = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "goal_reviewed")


def run(task="ship it", **kw):
    return asyncio.run(fakes.run_fake(FLOW, task, **kw))


def reviews(env, name):
    return {p.rsplit("/", 1)[1]: env.text(p) for p in env.files if p.startswith(".hmz/reviews/")
            and p.endswith(name)}


def test_a_passing_first_review_ends_the_run_on_it():
    actor, env = fakes.FakeAgentDriver(reply="done"), fakes.FakeEnvDriver()
    reviewer = fakes.FakeAgentDriver("codex", reply={"done": True, "notes": "checked"})

    out = run(agents={"actor": actor, "reviewer": reviewer}, local=env)

    assert out == "checked"
    assert actor.prompts == ["/goal ship it"]
    assert reviews(env, ".md") == {"review-1.md": "checked"}


def test_reviews_are_bounded_and_the_last_reviewer_is_told():
    actor = fakes.FakeAgentDriver(reply=["first", "second", "third"])
    reviewer = fakes.FakeAgentDriver(reply={"done": False, "notes": "fix 1"})
    env = fakes.FakeEnvDriver()

    out = run(agents={"actor": actor, "reviewer": reviewer}, local=env)

    assert out == "third"
    assert len(actor.prompts) == 3 and all(p.startswith("/goal ") for p in actor.prompts)
    first, last = (s.prompts[0] for s in reviewer.sessions)  # a fresh session per review
    assert "last review" not in first and "last review" in last
    assert "<agent_said>\nsecond\n</agent_said>" in last
    assert sorted(reviews(env, ".md")) == ["review-1.md", "review-2.md"]


def test_no_reviews_means_the_goal_alone():
    actor, reviewer = fakes.FakeAgentDriver(reply="done"), fakes.FakeAgentDriver()

    assert run(agents={"actor": actor, "reviewer": reviewer}, params={"max_reviews": 0}) == "done"
    assert reviewer.sessions == []


def test_the_users_choice_is_asked_and_goes_into_the_actors_next_goal():
    actor = fakes.FakeAgentDriver(reply=["done", "applied"])
    reviewer = fakes.FakeAgentDriver(reply=[
        {"done": True, "notes": "all fixed", "for_user": ["Keep X or drop it? I recommend keep."]},
        {"done": True, "notes": "applied as answered"}])
    user, env = fakes.FakeOutworlder(reply="1. drop it"), fakes.FakeEnvDriver()

    out = run(agents={"actor": actor, "reviewer": reviewer}, outworlder=user, local=env)

    assert out.startswith("applied as answered")
    assert len(user.asked) == 1
    asked = user.asks[0]
    assert "1. Keep X or drop it?" in str(asked)
    assert "carry out that answer" in actor.prompts[1]
    review = reviews(env, "review-1.md")["review-1.md"]
    assert "## For the user" in review and "## The user's answer\n\n1. drop it" in review
    assert reviews(env, "for-user.md") == {}


def test_with_nobody_there_the_choice_is_written_down_and_left_to_the_user():
    actor = fakes.FakeAgentDriver(reply=["done", "fixed"])
    reviewer = fakes.FakeAgentDriver(reply=[
        {"done": False, "notes": "1. typo", "for_user": ["Rerun or not?"]},
        {"done": True, "notes": "typo fixed"}])
    env = fakes.FakeEnvDriver()

    out = run(agents={"actor": actor, "reviewer": reviewer}, local=env)  # no outworlder: away

    assert out == "typo fixed"
    assert "leave them as they are" in actor.prompts[1]
    assert "1. Rerun or not?" in reviews(env, "for-user.md")["for-user.md"]


def test_a_passing_review_with_an_open_choice_and_nobody_there_ends_on_it():
    actor = fakes.FakeAgentDriver(reply="done")
    reviewer = fakes.FakeAgentDriver(reply={"done": True, "notes": "ok", "for_user": ["A or B?"]})
    env = fakes.FakeEnvDriver()

    out = run(agents={"actor": actor, "reviewer": reviewer}, local=env)

    assert out.startswith("ok\n\n## For the user") and actor.prompts == ["/goal ship it"]
    assert "for-user.md" in reviews(env, "for-user.md")


def test_the_actor_may_write_anywhere_and_the_reviewer_gets_the_default():
    actor, reviewer = fakes.FakeAgentDriver(reply="done"), fakes.FakeAgentDriver(
        reply={"done": True, "notes": "ok"})

    run(agents={"actor": actor, "reviewer": reviewer})

    assert actor.sessions[0].permission.preset == "danger-full-access"
    assert reviewer.sessions[0].permission.preset == "workspace-write"


def test_an_actor_whose_cli_has_no_goal_command_is_refused():
    with pytest.raises(CapabilityMissing):
        run(agents={"actor": fakes.FakeAgentDriver("pi"), "reviewer": fakes.FakeAgentDriver()})
