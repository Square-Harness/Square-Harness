# Square Harness — Docker download

This bundle runs the prebuilt Square Harness app. It does not contain the
application source or a language model. You need Docker Desktop on Windows or
macOS, or Docker Engine with the Compose plugin on Linux. Start Docker before
launching Square Harness.

## Start the app

1. Extract this ZIP to a folder.
2. From that folder, run one of these commands:

   **macOS or Linux**
   ```sh
   bash deployment/start-local.sh
   ```

   **Windows PowerShell**
   ```powershell
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\deployment\start-local.ps1
   ```

The launcher downloads the image for this release, starts the app, and opens
its private local link in your browser. Docker and an internet connection are
required. The app is available only on this computer at `127.0.0.1:8765`.

Do not share the private link: it grants access to the local workspace. The
app's image does not include or start a model. In the app, open **Model
provider**, select a provider, then set its endpoint, exact model name and API
key if required. Prompts and selected research material are sent to that model
provider. Check the provider's data and retention terms.

## Workspaces and saved data

The initial `default` workspace and all saved work live in the persistent
Docker volume `square-harness-local-data`, not in the folder where you
extracted this bundle. Click the workspace name above the message box to open
or create another workspace. To use files, add them through the app; the
container cannot browse arbitrary folders on your computer.

API keys are session-only by default. Local **Remember** stores a key in a
permission-restricted file in the Docker data volume. This is not encryption;
protect Docker data and its backups.

## Stop or update

From the folder where you extracted the bundle:

```sh
docker compose -p square-harness-local \
  --env-file deployment/compose.local.env.example down
```

This stops the app without deleting its data. To update to a later release,
download and extract that release's Docker bundle, then run its launcher. The
same named data volume is reused.

## Need development files?

This is the recommended app-only download. To modify Square Harness, contribute
code, or build/test a development image, use the source repository and its
development instructions:
<https://github.com/Square-Harness/Square-Harness>.
