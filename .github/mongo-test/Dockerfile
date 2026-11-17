# The MongoDB the CI test job runs the `mongo` tests against (ci.yml reads the
# FROM line below into its service container). It lives in a Dockerfile only
# so that Dependabot's docker ecosystem bumps the tag and digest together, as
# it does for the bot's own image; nothing builds this file.
FROM mongo:8@sha256:05b334b0a7a78fe913caac0cd2e1ecccf4da515d9a3a44be911b6ea3734c17cd
