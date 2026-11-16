# Publish ClipMind to the MCP Registry

`server.json` describes the Docker-distributed stdio server as
`io.github.yy31104/clipmind`. Committing this file does **not** list ClipMind in
the registry. A successful publish and a public registry response are required.
The registry stores metadata; the image remains on GitHub Container Registry.

## Release prerequisites

1. Finish the real-video benchmark and demo, and merge the reviewed release
   changes into `main`.
2. Publish the matching non-draft GitHub Release and public image, initially
   `v1.4.0` and `ghcr.io/yy31104/clipmind:v1.4.0`.
3. Check that the image can be pulled without registry credentials, and test
   its MCP initialization and tools using the command below. Making the source
   repository public does not automatically make a GHCR package public.
4. Confirm that `server.json`, the Python package version, image version and
   release tag agree. The image must carry these labels:

   ```text
   io.modelcontextprotocol.server.name=io.github.yy31104/clipmind
   org.opencontainers.image.version=1.4.0
   org.opencontainers.image.revision=<commit pointed to by v1.4.0>
   ```

The `clipmind` name on PyPI belongs to another project. This release uses GitHub
installation and GHCR rather than publishing over that name. A different PyPI
name would not by itself solve the current direct-URL dependency restriction.

## Run the published MCP image

After the public release image is available, configure an MCP client with:

```json
{
  "mcpServers": {
    "clipmind": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "--entrypoint", "clipmind-mcp",
        "--mount", "type=volume,src=clipmind-data,dst=/data",
        "ghcr.io/yy31104/clipmind:v1.4.0"
      ]
    }
  }
}
```

The entrypoint override is necessary: the same image normally starts the web
application. No host port is published for stdio MCP. `clipmind-data` is a
Docker-managed persistent library; `--rm` removes the container, not this
volume. The registry metadata mounts no host directory and exposes no arbitrary
mount-path setting. Files outside the container are not automatically readable.
To share an existing library, explicitly configure the same named volume in
the web container or use the native installation instead.

Extraction still needs network access, disk space for the evidence pack, and
local compute. First use may download models. Provider availability and source
access restrictions still apply; installing from a registry does not verify
every website or guarantee complete transcription/OCR. The Docker CPU path
does not imply access to the host's CUDA GPU, macOS Vision or logged-in Chrome.
Generated evidence stays in the volume until the user removes it. Acquisition
media follows ClipMind's normal cleanup rules.

## Publish deliberately

Run **Publish MCP Registry metadata** from the GitHub Actions page, selecting
`main`, or use:

```bash
gh workflow run mcp-publish.yml --repo yy31104/clipmind --ref main
```

This is a separate manual step after releasing the image. The workflow rejects
forks and non-main refs, verifies the published release and anonymous image
pull, checks image ownership/version/revision, and validates the metadata
before publishing. It authenticates using GitHub Actions OIDC, with only
`contents: read` and `id-token: write`; no registry PAT is needed.

`mcp-publisher` is pinned to `v1.8.1` with a fixed SHA-256 for the Linux amd64
archive and verification of the upstream Sigstore bundle. Action dependencies
are pinned to commit SHAs. Upgrade these pins deliberately after checking the
official release and schema compatibility; do not replace them with `latest`.

After the workflow succeeds, inspect the public record:

```bash
curl --fail 'https://registry.modelcontextprotocol.io/v0.1/servers/io.github.yy31104%2Fclipmind/versions/1.4.0'
```

Only then describe this release as listed. The registry is currently in
preview, so keep the direct GitHub/Docker installation instructions available.
An awesome-list submission is a separate maintainer-reviewed PR, not automatic
approval or endorsement by the MCP project.

## Official references

- [Registry quickstart](https://github.com/modelcontextprotocol/registry/blob/main/docs/modelcontextprotocol-io/quickstart.mdx)
- [OCI packages and image ownership](https://github.com/modelcontextprotocol/registry/blob/main/docs/modelcontextprotocol-io/package-types.mdx)
- [GitHub Actions and OIDC publishing](https://github.com/modelcontextprotocol/registry/blob/main/docs/modelcontextprotocol-io/github-actions.mdx)
- [server.json requirements](https://github.com/modelcontextprotocol/registry/blob/main/docs/reference/server-json/official-registry-requirements.md)
- [Pinned publisher release](https://github.com/modelcontextprotocol/registry/releases/tag/v1.8.1)
- [PyPI package with the occupied name](https://pypi.org/project/clipmind/)
- [PyPI direct URL dependency restriction](https://setuptools.pypa.io/en/latest/userguide/dependency_management.html#direct-url-dependencies)
