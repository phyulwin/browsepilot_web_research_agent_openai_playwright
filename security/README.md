# Chromium sandbox profile

`chromium-seccomp.json` is the Docker seccomp profile published by the Microsoft Playwright project, downloaded November 6, 2026 from:

<https://raw.githubusercontent.com/microsoft/playwright/main/utils/docker/seccomp_profile.json>

Source and license: [Microsoft Playwright](https://github.com/microsoft/playwright), Apache License 2.0; the repository's Apache license is included at `../LICENSE`.

The profile starts from Docker's default syscall restrictions and permits the namespace operations needed by Chromium's user-namespace sandbox. Compose runs the application as a non-root user with this profile, no added capabilities, and no-new-privileges; the application explicitly enables `chromium_sandbox=True`.

The host must permit unprivileged user namespaces; a provider blocking them cannot run this configuration unchanged. Do not disable Chromium's sandbox to work around a host restriction; use a compatible Docker host. See [Playwright's Docker guidance](https://playwright.dev/python/docs/docker).
