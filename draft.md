## Planned provider and deployment direction

The following decisions describe future work; they are not implemented yet.

| Area | Agreed direction | Still open |
| --- | --- | --- |
| Application image | Build one full-application Docker image, separate from the numerical sandbox. Docker Compose configuration selects local or server mode at startup; the same image is used in both modes. Mode selection is deployment configuration, not a UI control. | Compose file and environment-variable names |
| Provider interface | Keep workflow controllers unchanged. Add a common parent model-client contract and provider subclasses that implement or override provider-specific operations. Register providers centrally. | Exact method signatures and capability representation |
| Initial providers | Preserve existing backends/profiles: Ollama, OpenAI-compatible servers, llama.cpp, MLX, and DeepInfra. | Additional providers can be added later |
| Provider settings | Users supply a supported provider's API key and other provider-specific settings through the UI. Keys are session-only by default; an explicit Remember option enables persistence. | None |
| Local credentials | Use a permission-restricted file outside the image, workspace and job artifacts for remembered keys. | Exact host-mounted path and platform-specific permission handling |
| Local workspaces | Provide a password-free named-workspace selector on a trusted local computer. | None |
| Server identity | Require authentication and authorize every workspace operation. Accounts own their workspaces and provider settings. One deployment can serve multiple accounts; a container per account is not required. | Select a backend-verified identity provider. Managed OpenID Connect is recommended; React itself does not provide authentication |
| Android | Use Android as a client of the hosted Python backend. Agent work requires network access to the backend and model provider. | Choose installable web app or native wrapper |
| Usage controls | Enforce per-account concurrency, rate and usage limits in the application or an API gateway; provider quotas are an additional safeguard, not a substitute. | Choose limits and shared coordination mechanism for multi-replica deployments |

Server mode must fail closed if authentication and required server settings
are absent. A permission-restricted file does not isolate application accounts
when one backend process can read every user's files; server-side ownership
checks are mandatory, and persistent server credentials should use the selected
secret-management service.