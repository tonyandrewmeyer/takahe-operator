#!/usr/bin/env python3
# Copyright 2024 Tony Meyer
# See LICENSE file for licensing details.

"""Charm the Takahē instance."""

import logging
import random
import string

import ops
from charms.data_platform_libs.v0.data_interfaces import (
    DatabaseCreatedEvent,
    DatabaseRequires,
    SecretGroup,
)
from charms.grafana_k8s.v0.grafana_dashboard import GrafanaDashboardProvider
from charms.loki_k8s.v1.loki_push_api import LogProxyConsumer
from charms.prometheus_k8s.v0.prometheus_scrape import MetricsEndpointProvider
from charms.traefik_k8s.v2.ingress import IngressPerAppReadyEvent, IngressPerAppRequirer

logger = logging.getLogger(__name__)

DB_NAME = "takahē"


class TakahēOperatorCharm(ops.CharmBase):
    """Charm the Takahē instance."""

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)
        self.web_service_name = "takahe-web"
        self.background_service_name = "takahe-background"

        # Core setup:
        framework.observe(self.on.install, self._on_install)
        framework.observe(self.on.upgrade_charm, self._on_upgrade_charm)
        framework.observe(self.on.secret_changed, self._reset_services_or_defer)
        framework.observe(self.on.config_changed, self._reset_services_or_defer)
        framework.observe(self.on.collect_unit_status, self._on_collect_status)

        # Actions:
        framework.observe(self.on.add_superuser_action, self._on_add_superuser_action)
        framework.observe(self.on.cycle_secret_key_action, self._on_cycle_secret_key)

        # TODO: if I keep this like this, then this should be a dataclass or
        #       even a dict so that it's clear what each piece is.
        self.container_details = {
            self.web_service_name: (
                "Takahē web server",
                "gunicorn takahe.wsgi:application -b 0.0.0.0:8001 "
                "--access-logfile access.log --error-logfile error.log",
                False,
            ),
            self.background_service_name: (
                "Takahē background service",
                "python3 manage.py runstator",
                True,
            ),
        }

        # Container management:
        framework.observe(self.on[self.web_service_name].pebble_ready, self._on_pebble_ready)
        framework.observe(
            self.on[self.background_service_name].pebble_ready, self._on_pebble_ready
        )

        # Integrations:
        framework.observe(self.on.takahe_peer_relation_created, self._on_peer_relation_created)

        self.database = DatabaseRequires(self, relation_name="database", database_name=DB_NAME)
        framework.observe(self.database.on.database_created, self._on_database_created)
        framework.observe(self.database.on.endpoints_changed, self._reset_services_or_defer)

        self.ingress = IngressPerAppRequirer(self, port=8001)
        self.framework.observe(self.ingress.on.ready, self._on_ingress_ready)

        # TODO: There's no built-in metrics endpoint in takahē from what I can
        # tell. However, it would be straightforward to use the middleware from
        # https://pypi.org/project/django-prometheus/ to add it, either by using
        # a custom image or by doing an on-install tweak of the Django settings.
        self._prometheus_scraping = MetricsEndpointProvider(
            self,
            relation_name="metrics-endpoint",
            jobs=[{"static_configs": [{"targets": ["*:8444"]}]}],
            refresh_event=self.on.config_changed,
        )
        # TODO: there aren't any takahe log files by default - it supports logging to Sentry, but
        #       to fit the typical Juju pattern we would rather log to Loki. We can probably tweak
        #       the settings.py file (either a custom image or on install) to log to files and then
        #       push them to Loki.
        # TODO: it looks like maybe this is meant to be logs_scheme rather than log_files now?
        #       and maybe containers_syslog_port needs to specify the container?
        # TODO: The loki lib can set up Pebble log forwarding now, I believe.
        self._log_proxy = LogProxyConsumer(
            self,
            logs_scheme={
                "takahe-web": {
                    "log-files": ["/takahe/access.log", "/takahe/error.log"],
                    "syslog-port": 1514,
                },
                "takahe-background": {
                    "log-files": [],
                    "syslog-port": 1515,
                },
            },
            relation_name="log-proxy",
        )
        framework.observe(self._log_proxy.on.promtail_digest_error, self._on_promtail_error)

        # Provide grafana dashboards over a relation interface
        self._grafana_dashboards = GrafanaDashboardProvider(
            self, relation_name="grafana-dashboard"
        )
        # TODO: in src/grafana_dashboards put an appropriate .tmpl file.

    def _on_ingress_ready(self, event: IngressPerAppReadyEvent):
        # TODO: Do I actually need to do anything here?
        logger.info("This app's ingress URL: %s", event.url)

    def _on_promtail_error(self, event):
        logger.error(event.message)
        # TODO: This is what the interface docs have, but it won't work with
        # collect status!
        self.unit.status = ops.BlockedStatus(event.message)

    @property
    def peers(self):
        """Fetch the peer relation."""
        return self.model.get_relation("takahe-peer")

    def _on_collect_status(self, event: ops.CollectStatusEvent):  # noqa: C901
        # Default to everything is ok.
        event.add_status(ops.ActiveStatus())

        # Is the config ok?
        if not self.config["media-uri"].startswith(("local://", "s3://", "gcs://")):
            event.add_status(ops.BlockedStatus("Invalid 'media-uri' config value."))
        if not self.config.get("main-domain"):
            event.add_status(ops.BlockedStatus("Please set the main domain with `juju config`"))
        elif self.config["main-domain"] == "example.com":
            event.add_status(ops.BlockedStatus("example.com is not a valid main domain"))

        # Do we have the required storage?
        if self.model.storages:
            if "media-cache" not in self.model.storages:
                event.add_status(ops.WaitingStatus("Waiting for media-cache"))
            if self.config["media-uri"] == "local://" and "local-media" not in self.model.storages:
                event.add_status(ops.WaitingStatus("Waiting for local-media"))
        else:
            event.add_status(ops.WaitingStatus("Waiting for storage"))

        # Is the peer relation configured?
        if self.peers:
            # Do we have the required secrets?
            try:
                secret_id = self.peers.data[self.app]["secret-id"]
            except KeyError:
                event.add_status(ops.BlockedStatus("Waiting for peer secrets"))
            else:
                try:
                    self.model.get_secret(id=secret_id).get_content()
                except ops.SecretNotFoundError:
                    event.add_status(ops.BlockedStatus("Waiting for secret key"))
        else:
            event.add_status(ops.WaitingStatus("Waiting for peer relation"))

        # Check if we have everything we need to connect to the database.
        if not self.dsn:
            event.add_status(ops.BlockedStatus("Waiting for database relation"))

        # Is ingress configured?
        if not self.ingress.is_ready():
            event.add_status(ops.WaitingStatus("Waiting for ingress."))

    @staticmethod
    def _generate_secret_key(length=128):
        """Return a random sequence of characters suitable for a secret key."""
        choice = random.SystemRandom().choice
        return "".join(choice(string.ascii_uppercase + string.digits) for _ in range(length))

    def _on_install(self, event: ops.InstallEvent):
        if not self.unit.is_leader():
            logger.debug("Non-leader doesn't need to handle install.")
            return
        if not self.peers:
            logger.debug("Waiting for peer relation before creating secret key.")
            return
        self._add_secret_key()

    def _on_peer_relation_created(self, event: ops.RelationCreatedEvent):
        if not self.unit.is_leader():
            logger.debug("Non-leader doesn't need to handle peer-relation-created.")
            return
        self._add_secret_key()

    def _add_secret_key(self):
        """Generate a secret key for Takahē and store it as a Juju secret."""
        if not self.peers:
            logger.warning("Cannot add secret key before peer relation exists.")
            return
        # This is used for internal cryptography.
        secret_key = self._generate_secret_key()
        secret = self.unit.add_secret({"takahe-secret-key": secret_key})
        if secret.id is None:
            # TODO: Can this be tightened up in the ops type hinting?
            logger.warning("Juju secret is missing ID")
            return
        self.peers.data[self.app]["secret-id"] = secret.id
        logger.debug("Set Takahē secret key, id %s", secret.id)

    def _on_cycle_secret_key(self, event: ops.ActionEvent):
        if not self.unit.is_leader():
            event.fail("Please run this action on the leader unit.")
            return
        if not self.peers:
            event.fail("No need to cycle - we haven't even started up!")
            return
        try:
            secret_id = self.peers.data[self.app]["secret-id"]
        except KeyError:
            event.fail("No need to cycle - have not created the initial secret yet!")
            return
        try:
            secret = self.model.get_secret(id=secret_id)
        except ops.SecretNotFoundError:
            event.fail("No need to cycle - initial secret making in progress!")
            return

        new_secret_key = self._generate_secret_key()
        secret.set_content({"takahe-secret-key": new_secret_key})
        # The secret-changed event will take care of making use of the new revision.
        event.set_results({"result": "success"})

    def _on_add_superuser_action(self, event: ops.ActionEvent):
        # This can be done on either container, so use the background service
        # to minimise the impact.
        container = self.unit.get_container(self.background_service_name)
        env = self._takahē_env.copy()
        # Generate a random 32-character initial password.
        password = self._generate_secret_key(32)
        env["DJANGO_SUPERUSER_USERNAME"] = event.params["username"]
        env["DJANGO_SUPERUSER_PASSWORD"] = password
        env["DJANGO_SUPERUSER_EMAIL"] = event.params["email"]
        try:
            manage = container.exec(
                ["python3", "manage.py", "createsuperuser", "--no-input"],
                working_dir="/takahe",
                environment=env,
            )
            out, err = manage.wait_output()
            logger.info("Add superuser: %r, %r", out, err)
        except ops.pebble.ConnectionError:
            event.fail("Unable to connect to container.")
            return
        except ops.pebble.ExecError as e:
            logger.error("Unable to create super-user: %s", e)
            event.fail("Unable to add user.")
            return
        event.set_results({"initial-password": password})

    def _reset_services_or_defer(self, event: ops.SecretChangedEvent):
        try:
            self._replan(self.unit.get_container(self.background_service_name))
        except ops.pebble.ConnectionError:
            self.unit.status = ops.WaitingStatus(
                f"Unable to connect to {self.background_service_name}"
            )
            event.defer()
        except ops.pebble.ChangeError as e:
            self.unit.status = ops.BlockedStatus(
                f"Could not start {self.background_service_name}: {e}"
            )
            event.defer()
        try:
            self._replan(self.unit.get_container(self.web_service_name))
        except ops.pebble.ConnectionError:
            self.unit.status = ops.WaitingStatus(f"Unable to connect to {self.web_service_name}")
            event.defer()
        except ops.pebble.ChangeError as e:
            self.unit.status = ops.BlockedStatus(f"Could not start {self.web_service_name}: {e}")
            event.defer()

    def _on_pebble_ready(self, event: ops.PebbleReadyEvent):
        """Handle pebble-ready event."""
        container = event.workload
        try:
            self._replan(container)
        except ops.pebble.ConnectionError:
            self.unit.status = ops.WaitingStatus(f"Unable to connect to {container.name}")
            event.defer()
        except ops.pebble.ChangeError as e:
            self.unit.status = ops.BlockedStatus(f"Could not start {container.name}: {e}")
            event.defer()

    def _replan(self, container: ops.Container):
        summary, command, update_version = self.container_details[container.name]
        logger.debug("Updating plan for %s (%s -> %s)", container.name, summary, command)
        layer = ops.pebble.Layer(
            {
                "summary": summary,
                "description": "Service for Takahē ActivityPub instance",
                "services": {
                    container.name: {
                        "override": "replace",
                        "summary": summary,
                        "command": command,
                        "startup": "enabled",
                        "environment": self._takahē_env,
                    },
                },
            }
        )
        new_layer = layer.to_dict()
        services = container.get_plan().to_dict().get("services", {})
        if services != new_layer.get("services"):
            container.add_layer(container.name, layer, combine=True)
            logger.info("Added updated layer %r to Pebble plan.", container.name)
            container.replan()
            logger.info("Updated services.")

        if update_version:
            self.unit.set_workload_version(self.workload_version)

    @property
    def workload_version(self):
        """The version of Takahē installed in the containers."""
        logger.debug("Getting the workload version.")
        # This can be done on either container, so use the background service
        # to minimise the impact.
        container = self.unit.get_container(self.background_service_name)
        try:
            python = container.exec(
                ["python", "-c", "import takahe;print(takahe.__version__)"], working_dir="/takahe"
            )
            return python.wait_output()[0]
        except (ops.pebble.APIError, ops.pebble.ExecError) as e:
            logger.warning("Unable to get version from Takahē: %s", e)
            return "unknown"

    @property
    def dsn(self):
        """The string used to connect to the related PostgreSQL database."""
        if "database" not in self.model.relations or not self.model.relations["database"]:
            logger.debug("Cannot get DSN: relation is not ready.")
            return ""
        relation = self.model.relations["database"][0]
        # TODO: Is there some better way to do this? The secrets are provided as
        # attributes of the database events, but we need to provide them in the
        # environment every time we replan(). We don't really want to copy them
        # into a local secret. It seems like the interface sets a label, so we
        # can't just access them that way, unless we get the label, but that's
        # also hidden behind a private method.
        label = self.database._generate_secret_label("database", relation.id, SecretGroup.USER)
        secret = self.database.secrets.get(label)
        if not secret:
            logger.debug("Cannot get DSN: DB secret %s is not available.", label)
            return ""
        assert secret.meta is not None
        content = secret.meta.get_content()
        db_user = content["username"]
        db_password = content["password"]
        db_host = self.database.fetch_relation_field(relation.id, "endpoints")
        # TODO: I think we can request tls from the interface and then provide
        # tls and tls-ca here as well?
        return f"postgresql://{db_user}:{db_password}@{db_host}/{DB_NAME}?connect_timeout=10"

    @property
    def _takahē_env(self):
        # Peer data.
        if not self.peers:
            return {}
        takahē_secret_id = self.peers.data[self.app].get("secret-id")
        if not takahē_secret_id:
            return {}
        # We always refresh, because we can update any time - it just
        # invalidates sessions.
        try:
            takahē_secret = self.model.get_secret(id=takahē_secret_id).get_content(refresh=True)
        except ops.SecretNotFoundError:
            return {}

        # SMTP data.
        # TODO: There is an smtp-relay charm, but it's reactive, and I think
        # machine, and not publicly listed on charmhub. I should finish off the
        # exim-operator charm and use that (smtp-relay is postfix).

        # Put it all together.
        env = {
            "TAKAHE_DATABASE_SERVER": self.dsn,
            "TAKAHE_SECRET_KEY": takahē_secret["takahe-secret-key"],
            "TAKAHE_MEDIA_BACKEND": self.config["media-uri"],
            "TAKAHE_MAIN_DOMAIN": self.config.get("main-domain", ""),
            # "TAKAHE_EMAIL_SERVER": f"smtp://{urllib.parse.quote(smtp_username)}:{urllib.parse.quote(smtp_password)}@{smtp_host}:{smtp_port}/?tls=true",
            "TAKAHE_EMAIL_FROM": f"takahē@{self.config.get('main-domain', '')}",
            # TODO: It seems like this has to be provided even if not used.
            "TAKAHE_AUTO_ADMIN_EMAIL": f"takahē@{self.config.get('main-domain', '')}",  # Not used: there's an action to do this instead.
            "TAKAKE_USE_PROXY_HEADERS": "True",
            # TODO: handle push notifications, add TAKAHE_VAPID_PUBLIC_KEY and TAKAHE_VAPID_PRIVATE_KEY
            # TODO: consider others: https://docs.jointakahe.org/en/latest/tuning/
        }
        if self.config["media-uri"] == "local://":
            env["TAKAHE_MEDIA_ROOT"] = str(self.model.storages["local-media"][0].location)
            # This must be https:// something.
            env[
                "TAKAHE_MEDIA_URL"
            ] = "https://example.com/"  # TODO: must end with a slash - need to see up some sort of ingress (and server?) for this.
        return env

    def _on_upgrade_charm(self, event: ops.UpgradeCharmEvent):
        self.unit.status = ops.MaintenanceStatus("Upgrading database tables...")
        container = self.unit.get_container(self.background_service_name)
        try:
            # Update the tables & indexes.
            container.exec(["python3", "manage.py", "migrate"])
            manage = container.exec(
                ["python3", "manage.py", "migrate"],
                working_dir="/takahe",
                environment=self._takahē_env,
            )
            out, err = manage.wait_output()
            logger.info("DB upgrade: %r, %r", out, err)
            # Restart the services.
            self._replan(container)
        except ops.pebble.ConnectionError:
            self.unit.status = ops.WaitingStatus(f"Unable to connect to {container.name}")
            event.defer()
        except ops.pebble.ChangeError as e:
            self.unit.status = ops.BlockedStatus(f"Could not upgrade DB on {container.name}: {e}")
            event.defer()

        self.unit.status = ops.MaintenanceStatus("Restarting service...")
        try:
            self._replan(self.unit.get_container(self.web_service_name))
        except ops.pebble.ConnectionError:
            self.unit.status = ops.WaitingStatus(f"Unable to connect to {self.web_service_name}")
            event.defer()
        except ops.pebble.ChangeError as e:
            self.unit.status = ops.BlockedStatus(f"Could not start {self.web_service_name}: {e}")
            event.defer()

    def _on_database_created(self, event: DatabaseCreatedEvent):
        self.unit.status = ops.MaintenanceStatus("Creating database tables...")
        container = self.unit.get_container(self.background_service_name)
        try:
            # Create the tables & indexes.
            manage = container.exec(
                ["python3", "manage.py", "migrate"],
                working_dir="/takahe",
                environment=self._takahē_env,
            )
            out, err = manage.wait_output()
            logger.info("Initial DB install: %r, %r", out, err)
            # Restart the services.
            self._replan(container)
        except ops.pebble.ConnectionError:
            self.unit.status = ops.WaitingStatus(f"Unable to connect to {container.name}")
            event.defer()
        except ops.pebble.ChangeError as e:
            self.unit.status = ops.BlockedStatus(
                f"Could not initialise DB on {container.name}: {e}"
            )
            event.defer()

        self.unit.status = ops.MaintenanceStatus("Starting service...")
        try:
            self._replan(self.unit.get_container(self.web_service_name))
        except ops.pebble.ConnectionError:
            self.unit.status = ops.WaitingStatus(f"Unable to connect to {self.web_service_name}")
            event.defer()
        except ops.pebble.ChangeError as e:
            self.unit.status = ops.BlockedStatus(f"Could not start {self.web_service_name}: {e}")
            event.defer()


if __name__ == "__main__":  # pragma: nocover
    ops.main(TakahēOperatorCharm)  # type: ignore
