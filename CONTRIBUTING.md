# Contributing

To make contributions to this charm, you'll need a working [development setup](https://juju.is/docs/sdk/dev-setup).

You can create an environment for development with `tox`:

```shell
tox devenv -e integration
source venv/bin/activate
```

## Testing

This project uses `tox` for managing test environments. There are some pre-configured environments
that can be used for linting and formatting code when you're preparing contributions to the charm:

```shell
tox run -e format        # update your code according to linting rules
tox run -e lint          # code style
tox run -e static        # static type checking
tox run -e unit          # unit tests
tox run -e integration   # integration tests
tox                      # runs 'format', 'lint', 'static', and 'unit' environments
```

## Build the charm

Build the charm in this git repository using:

```shell
charmcraft pack
```

## Deploy the charm

1. Deploy the charm itself

```shell
juju deploy ./takahe-k8s_ubuntu-22.04-amd64.charm --resource takahe-image=jointakahe/takahe
```

2. Deploy postgresql-k8s and traefik-k8s

```shell
juju deploy postgresql-k8s --channel=14/stable --trust
juju deploy traefik-k8s
```

3. Integrate takahē with postgresql and traefik:

```shell
juju integrate takahe-k8s postgresql-k8s
juju integrate takahe-k8s traefik-k8s
```

# Notes

### Places where non-ASCII characters don't work

* Charm name: "Invalid instance name: Name can only contain alphanumeric and hyphen characters"
* Juju model names: "model names may only contain lowercase letters, digits and hyphens"
* Juju relations: "ERROR "takahe-operator:takahē-peer" is not a valid relation key" (this left the model in a very broken state that I couldn't fix even with remove-application --force)
* Juju container & images (hangs installing)
* Secret keys: Invalid secret keys: ['takahē-secret-key']. Keys should be lowercase letters and digits, at least 3 characters long, start with a letter, and not start or end with a hyphen.
* GitHub repo names.

### Things linting should catch

* Invalid storage definition (really should just validate all of charmcraft.yaml)

<!-- You may want to include any contribution/style guidelines in this document>
