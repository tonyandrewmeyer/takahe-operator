<!--
Avoid using this README file for information that is maintained or published elsewhere, e.g.:

* metadata.yaml > published on Charmhub
* documentation > published on (or linked to from) Charmhub
* detailed contribution guide > documentation or CONTRIBUTING.md

Use links instead.
-->

# Takahē Operator

[![CharmHub Badge](https://charmhub.io/takahe-k8s/badge.svg)](https://charmhub.io/takahe-k8s)

A [Juju](https://juju.is/) [charm](https://juju.is/docs/olm/charmed-operators) deploying and managing Takahē on Kubernetes. [Takahē](https://jointakahe.org) is an ActivityPub server, and it's free and open-source.

Takahē can interact with other ActivityPub servers in the 'Fediverse', such as Mastodon,
Lemmy, micro.blog, or PeerTube. Takahē is fairly straightforward to install and manage,
but this charm simplifies initial deployment and "day N" operations of Takahē on Kubernetes.
It allows for deployment on many different Kubernetes platforms, from
[MicroK8s](https://microk8s.io/) to [Charmed Kubernetes](https://ubuntu.com/kubernetes)
to public cloud Kubernetes offerings.

As such, the charm makes it easy for those looking to take control of their own ActivityPub server
whilst keeping operations simple, and gives them the freedom to deploy on the Kubernetes platform
of their choice.

For DevOps or SRE teams this charm will make operating Takahē simple and straightforward through
Juju's clean interface. It will allow easy deployment into multiple environments for testing of
changes, and supports scaling out for enterprise deployments.

This charm is intended for use by anyone wishing to manage a Takahē instance, generally
for their own private use, with one or more domains.

Note that this charm aims to make installation and management of your Takahē server simple,
but there are other aspects to running an ActivityPub server that you must handle. In
particular, supporting your users, moderation, defederation (if needed), knowing how to
handle illegal content, and so on.

## Other resources

<!-- If your charm is documented somewhere else other than Charmhub, provide a link separately. -->

- [Read more](https://jointakahe.org) (note that this charm is not official or affiliated with Takahē)

- [Code of conduct](https://ubuntu.com/community/code-of-conduct)

- [Contributing](CONTRIBUTING.md)

- See the [Juju SDK documentation](https://juju.is/docs/sdk) for more information about developing and improving charms.
