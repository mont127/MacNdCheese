# Security Policy

MacNdCheese is a tool that manages Wine and graphics dependencies to help run Windows games on macOS.

Security matters because the project downloads and runs external components such as Wine DXVK and Mesa.

## Supported versions

Security fixes are provided for the latest released version only.

Current supported release line is MacNdCheese v4.2.0+.

Older releases such as v2.0.0 v3.0.0 v1.2.0 v1.1.0 and v1.0.0 are not supported for security fixes.

If you are using an older release please upgrade to the latest v3.x+ release before reporting a security issue.

## Reporting a vulnerability

If you discover a security issue please report it responsibly.

Do not open a public issue for vulnerabilities that could put users at risk.

Instead contact the maintainer directly.

Include this information.

Description of the issue.
Steps to reproduce.
Affected version.
Affected component.
Expected behavior.
Actual behavior.
Impact if exploited.
Proof of concept details if safe to share privately.
Logs or screenshots if available.

If the issue involves downloads include the exact URL used and any checksum information you verified.

## What counts as a security issue

A security issue includes problems like these.

Downloading binaries from untrusted sources.
Hidden network activity.
Command injection possibilities.
Unsafe shell execution.
Improper handling of user supplied paths.
Privilege escalation through the tool.
Unsafe file writes outside expected directories.
Tampering risk in packaged releases.

## Response process

When a vulnerability is reported this is the process.

The report is acknowledged.
The issue is verified.
Risk level is evaluated.
A fix is prepared.
A patched release is published.
Users are informed in the repository.

## Responsible disclosure

Please avoid publishing details until a fix is released.

This helps protect users.

## Security guidelines for contributors

Do not add hidden downloads.

Always use reputable release sources for dependencies.

Avoid executing shell commands built from untrusted input.

Prefer safe subprocess usage and strict quoting.

Make all downloads visible to the user and logged by the application.

Document any network request clearly.
