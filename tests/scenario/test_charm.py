#! /usr/bin/env python

import ops
import pytest
import scenario
import yaml
from charm import TakahēOperatorCharm


@pytest.fixture
def ctx():
    # TODO: doesn't scenario have something for this?
    with open("charmcraft.yaml") as metadata:
        meta = yaml.safe_load(metadata)
    return scenario.Context(
        TakahēOperatorCharm, meta=meta, actions=meta["actions"], config=meta["config"]
    )


def test_add_superuser_action(ctx):
    relation = scenario.PeerRelation(
        "takahe-peer",
        "takahe-peer",
    )
    # SCENARIO-NOTE: I can't check that the working directory is properly set here, or that the
    # environment is properly set up (which also means I can't check that the password provided
    # is the same one given to Django).
    container = scenario.Container(
        "takahe-background",
        can_connect=True,
        exec_mock={
            ("python3", "manage.py", "createsuperuser", "--no-input"): scenario.ExecOutput(
                return_code=0
            )
        },
    )
    state = scenario.State(relations=[relation], containers=[container])
    user = "USER"
    email = "EMAIL"
    out = ctx.run_action(
        scenario.Action("add-superuser", params={"username": user, "email": email}), state=state
    )
    assert out.success
    assert "initial-password" in out.results


def test_cycle_secret_key_action_nonleader(ctx):
    state = scenario.State(leader=False)
    out = ctx.run_action("cycle-secret-key", state=state)
    assert not out.success
    assert "leader" in out.failure


def test_cycle_secret_key_action_no_peers(ctx):
    state = scenario.State(leader=True)
    out = ctx.run_action("cycle-secret-key", state=state)
    assert not out.success


def test_cycle_secret_key_action_no_peer_data(ctx):
    relation = scenario.PeerRelation("takahe-peer", "takahe-peer")
    state = scenario.State(leader=True, relations=[relation])
    out = ctx.run_action("cycle-secret-key", state=state)
    assert not out.success


def test_cycle_secret_key_action_no_secret_yet(ctx):
    relation = scenario.PeerRelation(
        "takahe-peer", "takahe-peer", local_app_data={"secret-id": "1234"}
    )
    state = scenario.State(leader=True, relations=[relation])
    out = ctx.run_action("cycle-secret-key", state=state)
    assert not out.success


def test_cycle_secret_key_action_leader(ctx):
    secret = scenario.Secret("1234", {0: {"takahe-secret-key": "friend"}}, owner="unit")
    relation = scenario.PeerRelation(
        "takahe-peer", "takahe-peer", local_app_data={"secret-id": "1234"}
    )
    state = scenario.State(leader=True, relations=[relation], secrets=[secret])
    out = ctx.run_action("cycle-secret-key", state=state)
    assert out.success
    assert out.results == {"result": "success"}
    # Verify that the key has actually changed.
    assert len(out.state.secrets[0].contents) == 2
    assert out.state.secrets[0].contents[0] != out.state.secrets[0].contents[1]


def test_start(ctx):
    # Start doesn't actually do anything currently. Verify that there are no
    # problems, and we end up with an blocked status.
    state = scenario.State()
    out = ctx.run("start", state)
    assert out.unit_status.name == "blocked"


def test_install_non_leader(ctx):
    # Only the leader does anything for install, so this should just do nothing
    # and end up with an blocked status.
    state = scenario.State(leader=False)
    out = ctx.run("install", state)
    assert out.unit_status.name == "blocked"


def test_install_leader(ctx):
    relation = scenario.PeerRelation("takahe-peer", "takahe-peer")
    state = scenario.State(leader=True, relations=[relation])
    out = ctx.run("install", state)
    # At this point we are still blocked, waiting for the config.
    assert out.unit_status.name == "blocked"
    assert len(out.secrets) == 1
    # SCENARIO-NOTE: The [0] here is the revision, I think, which seems ugly.
    # Also, why a dict and not a list if this is the way?
    assert "takahe-secret-key" in out.secrets[0].contents[0]
    assert len(out.secrets[0].contents[0]["takahe-secret-key"]) == 128
    assert out.relations[0].local_app_data["secret-id"] == out.secrets[0].id


def test_peer_created_non_leader(ctx):
    # Only the leader does anything for peer-relation-created, so this should just do nothing
    # and end up with an blocked status.
    relation = scenario.PeerRelation("takahe-peer", "takahe-peer")
    state = scenario.State(leader=False, relations=[relation])
    out = ctx.run(relation.created_event, state)
    assert out.unit_status.name == "blocked"


def test_peer_created_leader(ctx):
    relation = scenario.PeerRelation("takahe-peer", "takahe-peer")
    state = scenario.State(leader=True, relations=[relation])
    out = ctx.run(relation.created_event, state)
    # At this point we are still blocked, waiting for the config.
    assert out.unit_status.name == "blocked"
    assert len(out.secrets) == 1
    # SCENARIO-NOTE: The [0] here is the revision, I think, which seems ugly.
    # Also, why a dict and not a list if this is the way?
    assert "takahe-secret-key" in out.secrets[0].contents[0]
    assert len(out.secrets[0].contents[0]["takahe-secret-key"]) == 128
    assert out.relations[0].local_app_data["secret-id"] == out.secrets[0].id


def test_pebble_ready_web(ctx):
    ## Arrange.
    domain = "aotearoa.dev"
    media = "local://"
    user = "USER"
    password = "PASS"
    endpoint = "db.local:8000"
    secret = scenario.Secret(
        "1234", {0: {"takahe-secret-key": "old-secret"}, 1: {"takahe-secret-key": "friend"}}
    )
    web_container = scenario.Container(
        "takahe-web",
        can_connect=True,
    )
    storage = scenario.Storage("local-media")
    peer_relation = scenario.PeerRelation(
        "takahe-peer", "takahe-peer", local_app_data={"secret-id": secret.id}
    )
    database_relation = scenario.Relation(
        "database", "postgresql_client", remote_app_data={"endpoints": endpoint}
    )
    # SCENARIO-NOTE: It would be nice if the charm test didn't need to know the
    # layout, since the lib is hiding all of that - but maybe that's really an
    # issue with how the lib hides everything.
    db_secret = scenario.Secret(
        "1235",
        {0: {"username": user, "password": password}},
        label=f"database.{database_relation.relation_id}.user.secret",
    )
    state = scenario.State(
        leader=True,
        relations=[peer_relation, database_relation],
        containers=[web_container],
        storage=[storage],
        secrets=[secret, db_secret],
        config={"main-domain": domain, "media-uri": media},
    )

    ## Act.
    out = ctx.run(web_container.pebble_ready_event, state)

    ## Assert.
    cmd = (
        "gunicorn takahe.wsgi:application -b 0.0.0.0:8001 "
        "--access-logfile access.log --error-logfile error.log"
    )
    assert_running_state(
        ctx,
        out,
        out.containers[0],
        user,
        password,
        endpoint,
        secret.contents[1]["takahe-secret-key"],
        domain,
        media,
        "Takahē web server",
        cmd,
        web_container.name,
    )


def test_pebble_ready_background(ctx):
    ## Arrange.
    version = "1.2.3"
    domain = "aotearoa.dev"
    media = "local://"
    user = "USER"
    password = "PASS"
    endpoint = "db.local:8000"
    secret = scenario.Secret(
        "1234", {0: {"takahe-secret-key": "old-secret"}, 1: {"takahe-secret-key": "friend"}}
    )
    background_container = scenario.Container(
        "takahe-background",
        can_connect=True,
        exec_mock={
            ("python", "-c", "import takahe;print(takahe.__version__)"): scenario.ExecOutput(
                return_code=0, stdout=version
            )
        },
    )
    storage = scenario.Storage("local-media")
    peer_relation = scenario.PeerRelation(
        "takahe-peer", "takahe-peer", local_app_data={"secret-id": secret.id}
    )
    database_relation = scenario.Relation(
        "database", "postgresql_client", remote_app_data={"endpoints": endpoint}
    )
    # SCENARIO-NOTE: It would be nice if the charm test didn't need to know the
    # layout, since the lib is hiding all of that - but maybe that's really an
    # issue with how the lib hides everything.
    db_secret = scenario.Secret(
        "1235",
        {0: {"username": user, "password": password}},
        label=f"database.{database_relation.relation_id}.user.secret",
    )
    state = scenario.State(
        leader=True,
        relations=[peer_relation, database_relation],
        containers=[background_container],
        storage=[storage],
        secrets=[secret, db_secret],
        config={"main-domain": domain, "media-uri": media},
    )

    ## Act.
    out = ctx.run(background_container.pebble_ready_event, state)

    ## Assert.
    cmd = "python3 manage.py runstator"
    assert_running_state(
        ctx,
        out,
        out.containers[0],
        user,
        password,
        endpoint,
        secret.contents[1]["takahe-secret-key"],
        domain,
        media,
        "Takahē background service",
        cmd,
        background_container.name,
    )
    assert out.workload_version == version


def assert_running_state(
    ctx,
    out,
    container,
    db_user,
    db_password,
    db_host,
    secret_key,
    domain,
    media,
    summary,
    cmd,
    service_name,
):
    takahē_env = {
        "TAKAHE_DATABASE_SERVER": f"postgresql://{db_user}:{db_password}@{db_host}/takahē?connect_timeout=10",
        "TAKAHE_SECRET_KEY": secret_key,
        "TAKAHE_MEDIA_BACKEND": media,
        "TAKAHE_MAIN_DOMAIN": domain,
        "TAKAHE_EMAIL_FROM": f"takahē@{domain}",
        "TAKAHE_AUTO_ADMIN_EMAIL": f"takahē@{domain}",
        "TAKAKE_USE_PROXY_HEADERS": "True",
        "TAKAHE_MEDIA_ROOT": str(out.storage[0].get_filesystem(ctx)),
        "TAKAHE_MEDIA_URL": "https://example.com/",
    }
    # SCENARIO-NOTE: it would be nice if there was a cleaner way to create the
    # plan object.
    raw_plan = yaml.safe_dump(
        {
            "summary": summary,
            "description": "Service for Takahē ActivityPub instance",
            "services": {
                service_name: {
                    "override": "replace",
                    "summary": summary,
                    "command": cmd,
                    "startup": "enabled",
                    "environment": takahē_env,
                },
            },
        }
    )
    expected_plan = ops.pebble.Plan(raw_plan)
    # SCENARIO-NOTE: maybe actually an ops note: it would be nice if a Plan had
    # an __eq__ and basically did this.
    assert container.plan.to_dict() == expected_plan.to_dict()
    assert container.service_status[service_name] == ops.pebble.ServiceStatus.ACTIVE


@pytest.mark.parametrize("container_name", ("takahe-web", "takahe-background"))
def test_pebble_ready_no_connect(ctx, container_name):
    container = scenario.Container(container_name)
    state = scenario.State(
        leader=True,
        containers=[container],
        relations=[scenario.PeerRelation("takahe-peer", "takahe-peer")],
    )
    out = ctx.run(container.pebble_ready_event, state)
    assert out.deferred[0].name == f"{container_name.replace('-', '_')}_pebble_ready"


# TODO: endpoints_changed should trigger this as well, but I'm not sure exactly
#       how to get the lib to emit that event.
@pytest.mark.parametrize("event_name", ("secret_changed", "config_changed"))
def test_completely_installed(ctx, event_name):
    ## Arrange.
    version = "1.2.3"
    domain = "aotearoa.dev"
    media = "local://"
    user = "USER"
    password = "PASS"
    endpoint = "db.local:8000"
    secret = scenario.Secret("1234", {0: {"takahe-secret-key": "friend"}})
    web_container = scenario.Container(
        "takahe-web",
        can_connect=True,
    )
    background_container = scenario.Container(
        "takahe-background",
        can_connect=True,
        exec_mock={
            ("python", "-c", "import takahe;print(takahe.__version__)"): scenario.ExecOutput(
                return_code=0, stdout=version
            )
        },
    )
    storage = scenario.Storage("local-media")
    peer_relation = scenario.PeerRelation(
        "takahe-peer", "takahe-peer", local_app_data={"secret-id": secret.id}
    )
    database_relation = scenario.Relation(
        "database", "postgresql_client", remote_app_data={"endpoints": endpoint}
    )
    # SCENARIO-NOTE: It would be nice if the charm test didn't need to know the
    # layout, since the lib is hiding all of that - but maybe that's really an
    # issue with how the lib hides everything.
    db_secret = scenario.Secret(
        "1235",
        {0: {"username": user, "password": password}},
        label=f"database.{database_relation.relation_id}.user.secret",
    )
    state = scenario.State(
        leader=True,
        relations=[peer_relation, database_relation],
        containers=[web_container, background_container],
        storage=[storage],
        secrets=[secret, db_secret],
        config={"main-domain": domain, "media-uri": media},
    )

    ## Act.
    if event_name == "secret_changed":
        event = secret.changed_event
    else:
        event = event_name
    out = ctx.run(event, state)

    ## Assert.
    cmd = (
        "gunicorn takahe.wsgi:application -b 0.0.0.0:8001 "
        "--access-logfile access.log --error-logfile error.log"
    )
    assert_running_state(
        ctx,
        out,
        out.containers[0],
        user,
        password,
        endpoint,
        secret.contents[0]["takahe-secret-key"],
        domain,
        media,
        "Takahē web server",
        cmd,
        web_container.name,
    )
    assert_running_state(
        ctx,
        out,
        out.containers[1],
        user,
        password,
        endpoint,
        secret.contents[0]["takahe-secret-key"],
        domain,
        media,
        "Takahē background service",
        "python3 manage.py runstator",
        background_container.name,
    )
    assert out.workload_version == version


def test_upgrade_charm(ctx):
    ## Arrange.
    version = "1.2.3"
    domain = "aotearoa.dev"
    media = "local://"
    user = "USER"
    password = "PASS"
    endpoint = "db.local:8000"
    secret = scenario.Secret("1234", {0: {"takahe-secret-key": "friend"}})
    web_container = scenario.Container(
        "takahe-web",
        can_connect=True,
    )
    background_container = scenario.Container(
        "takahe-background",
        can_connect=True,
        exec_mock={
            ("python", "-c", "import takahe;print(takahe.__version__)"): scenario.ExecOutput(
                return_code=0, stdout=version
            ),
            ("python3", "manage.py", "migrate"): scenario.ExecOutput(return_code=0),
        },
    )
    storage = scenario.Storage("local-media")
    peer_relation = scenario.PeerRelation(
        "takahe-peer", "takahe-peer", local_app_data={"secret-id": secret.id}
    )
    database_relation = scenario.Relation(
        "database", "postgresql_client", remote_app_data={"endpoints": endpoint}
    )
    # SCENARIO-NOTE: It would be nice if the charm test didn't need to know the
    # layout, since the lib is hiding all of that - but maybe that's really an
    # issue with how the lib hides everything.
    db_secret = scenario.Secret(
        "1235",
        {0: {"username": user, "password": password}},
        label=f"database.{database_relation.relation_id}.user.secret",
    )
    state = scenario.State(
        leader=True,
        relations=[peer_relation, database_relation],
        containers=[web_container, background_container],
        storage=[storage],
        secrets=[secret, db_secret],
        config={"main-domain": domain, "media-uri": media},
    )

    ## Act.
    out = ctx.run("upgrade_charm", state)

    ## Assert.
    cmd = (
        "gunicorn takahe.wsgi:application -b 0.0.0.0:8001 "
        "--access-logfile access.log --error-logfile error.log"
    )
    assert_running_state(
        ctx,
        out,
        out.containers[0],
        user,
        password,
        endpoint,
        secret.contents[0]["takahe-secret-key"],
        domain,
        media,
        "Takahē web server",
        cmd,
        web_container.name,
    )
    assert_running_state(
        ctx,
        out,
        out.containers[1],
        user,
        password,
        endpoint,
        secret.contents[0]["takahe-secret-key"],
        domain,
        media,
        "Takahē background service",
        "python3 manage.py runstator",
        background_container.name,
    )
    assert out.workload_version == version
    # SCENARIO-NOTE: it feels a bit odd to have to include the UnknownStatus
    # here, although I understand that it's because it's what was set (or not
    # in this case) in the input state).)
    assert ctx.unit_status_history == [
        ops.UnknownStatus(),
        ops.MaintenanceStatus("Upgrading database tables..."),
        ops.MaintenanceStatus("Restarting service..."),
    ]


def test_database_ready(ctx):
    ## Arrange.
    version = "1.2.3"
    domain = "aotearoa.dev"
    media = "local://"
    user = "USER"
    password = "PASS"
    endpoint = "db.local:8000"
    secret = scenario.Secret("1234", {0: {"takahe-secret-key": "friend"}})
    web_container = scenario.Container(
        "takahe-web",
        can_connect=True,
    )
    background_container = scenario.Container(
        "takahe-background",
        can_connect=True,
        exec_mock={
            ("python", "-c", "import takahe;print(takahe.__version__)"): scenario.ExecOutput(
                return_code=0, stdout=version
            ),
            ("python3", "manage.py", "migrate"): scenario.ExecOutput(return_code=0),
        },
    )
    storage = scenario.Storage("local-media")
    peer_relation = scenario.PeerRelation(
        "takahe-peer", "takahe-peer", local_app_data={"secret-id": secret.id}
    )
    database_relation = scenario.Relation(
        "database", "postgresql_client", remote_app_data={"endpoints": endpoint}
    )
    # SCENARIO-NOTE: It would be nice if the charm test didn't need to know the
    # layout, since the lib is hiding all of that - but maybe that's really an
    # issue with how the lib hides everything.
    db_secret = scenario.Secret(
        "1235",
        {0: {"username": user, "password": password}},
        label=f"database.{database_relation.relation_id}.user.secret",
    )
    state = scenario.State(
        leader=True,
        relations=[peer_relation, database_relation],
        containers=[web_container, background_container],
        storage=[storage],
        secrets=[secret, db_secret],
        config={"main-domain": domain, "media-uri": media},
    )

    ## Act.
    out = ctx.run(database_relation.changed_event, state)

    ## Assert.
    cmd = (
        "gunicorn takahe.wsgi:application -b 0.0.0.0:8001 "
        "--access-logfile access.log --error-logfile error.log"
    )
    assert_running_state(
        ctx,
        out,
        out.containers[0],
        user,
        password,
        endpoint,
        secret.contents[0]["takahe-secret-key"],
        domain,
        media,
        "Takahē web server",
        cmd,
        web_container.name,
    )
    assert_running_state(
        ctx,
        out,
        out.containers[1],
        user,
        password,
        endpoint,
        secret.contents[0]["takahe-secret-key"],
        domain,
        media,
        "Takahē background service",
        "python3 manage.py runstator",
        background_container.name,
    )
    assert out.workload_version == version
