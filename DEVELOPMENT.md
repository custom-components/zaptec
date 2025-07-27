# Development of the Zaptec HA integration

This document intends to contain various documentation and tips&tricks for
developing the integration.


## Guidelines

This is a collections of guidelines that should be considered when working with

- Use ruff format to autoformat the sources. This is what HA is using and its
  best if this integration is doing the same.

- When adding a new entity, check that its a "state" variable. The state vars
  are polled much more frequent than any of the other. Some vars exists in either
  under slightly different names and then the state variant should be preferred.

- Use the HA quality scale guidelines:
  https://developers.home-assistant.io/docs/core/integration-quality-scale/rules


## Linting and formatting

The codebase in this project should be aligned with the formatting and linting
rules of Home Assistant. `ruff` is being used as the choice of linter.

The ruff settings are not precisely the same as HA because HA have an very
extensive rulelist. When encountering linting error in this project, it might
be that the rule is too strict. If HA have added an exception for the rule, we
should be inclined to follow suit.

- https://github.com/home-assistant/core/blob/dev/pyproject.toml#L652


# Setup development environment

This section goes through the steps of setting up the development environment
using VSCode and Dev containers.

## Prerequisites

- Git. For Windows: https://gitforwindows.org/index.html

- VS Code. https://code.visualstudio.com/

- Install "Dev Containers" extension into VS Code,
  https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers

- Windows: WSL2 https://learn.microsoft.com/en-us/windows/wsl/install

- GitHub account (if you are going to make contributions), https://github.com

- If on Windows with WSL2, install a Linux of choice. Open a command-prompt
  and enter

        wsl --install -d Ubuntu  # Or the distro you prefer

## GitHub fork

If you are going to make contributions to Zaptec, you need to fork a copy
of the Zaptec repo.

1. Log in or create a GitHub user

2. Navigate to https://github.com/custom-components/zaptec

3. Fork th repo, by pressing the "Fork" button

4. Press the green "<> Code" button and copy the URL.

You now have your own copy, a fork, of the repo. This is going to be the basis
for developing from. You can use either HTTPS or SSH URL of your choice. Please
see the documentation for git in how to set that up.


## Setting up the dev container

1. Check out Zaptec at a suitable location

       # Here you can use your own fork address of zaptec
       git clone https://github.com/custom-components/zaptec.git

2. Open VS Code and select "Open folder" (Ctrl+K Ctrl+O) and navigate to the
   directiory with the zaptec integration

3. If VS Code asks "There is a Dev Container configuration available for this
   repository. Reopen folder to develop in a container (learn more)." then
   press "Reopen in container". Otherwise press F1 to open the command prompt
   and enter "Dev Containers: Rebuild and reopen in container"

4. If this is the first time VS Code will ask: "Dev Container require Docker to
   run. Do you want to install Docker in WSL?" press yes.

This process takes quite a long time the first time. When complete, you now
have a development environment that is setup for developing. VS Code is setup
with the proper python, the proper intellisense and the proper linting rules.

More infor about dev containers:

- https://code.visualstudio.com/docs/devcontainers/containers
- https://developers.home-assistant.io/docs/development_environment/


## Running Home Assistant

To run the Home Assistant, the task is simple:

1. Press F1 and select "Tasks: Run Task" and press enter.

2. Select "Run Home Assistant on port 8123". Wait a few minutes until it
   reads "Home Assistant started" in the logs.

3. Open a web browser of choice and open "http://localhost:8123"

4. The first time you need to fill in a few questions to configure HA.

5. To enable Zaptec, you need to set it up the same way any new integration is
   done: Settings -> Devices & Services -> + Add integration (blue button) and
   then write "Zaptec".

The Zaptec integration is running in debug mode, so the log output will
contain a lot of info.


# Zaptec model and behaviors

This secion describes various behaviors of the Zaptec API that might have an
impact in the implementation.


## Zaptec constants

The Zaptec API relies on a constant file which is used to look up settings
and number to string tables. This can be downloaded either from downloading

https://api.zaptec.com/api/constants

Or by the snippet:

```py
import json
async with Zaptec(username, password) as zaptec:
    with open("constants.json", "w") as fp:
        json.dump(await zaptec.request("constants"), fp, indent=2)
```


## Resume charging when authorization required

When authorization required is enabled the restart of charging takes two steps
instead of one.

`State: Connected_Finished --> Command: ResumeCharging (507) --> State:
Connected_Requesting --> Command: AuthorizeCharge --> State: Connected_Charging`

Last observed Jul 2025


## Command DeAuthorizeAndStop (10001) fails with 500

Currently issuing the command DeauthorizeAndStop (code 10001 or
"deauthorize_and_stop" in this integration) results in the HTTP error 500
Internal server error. Despite the error, the command will be excuted.

Last observed Jul 2025


## Error codes on 500 Internal server error

When the Zaptec cloud return HTTP error 500 Internal server error, the payload
will contain additional information. See the [discussion about 500 errors](
https://github.com/custom-components/zaptec/issues/176#issuecomment-3104485671)

```json
{"Code":528,"Details":null,"StackTrace":null}
```

It seems the error code can be looked up in constants under `ErrorCodes`, where
528 corresponds to `DeviceCommandRejected`.

Last observed Jul 2025


## SignedMeterValue

The field `SignedMeterValue` and `CompletedSession.SignedSession` are using the
Open Charge Metering Format, which can be read here:

* https://github.com/SAFE-eV/OCMF-Open-Charge-Metering-Format/blob/master/OCMF-en.md
