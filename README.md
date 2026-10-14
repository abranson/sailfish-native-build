# sailfish-native-build

`mbn` builds Sailfish RPMs directly in your source checkout using the host's
native compiler and installed Sailfish release. It supports matching ARM32,
ARM64, x86 and x86-64 environments. Install the `sailfish-native-build` RPM to
get the command and its required tools.

## Build

From the directory containing `rpm/`, run as your normal development user:

```sh
mbn build
```

This reads the spec's `BuildRequires`, prompts to install missing dependencies
through `devel-su` and zypper, then runs `rpmbuild` and checks the resulting RPMs.
Dependencies are installed on the host. Repository metadata must be current.

Build output appears live on stdout. Existing object files are reused where
the project's build system supports incremental builds. Initialize required
submodules before building; the spec and build system can modify your checkout.

- RPMs go directly into `RPMS/`. Rebuilding the same filename replaces it.
- Logs and the build manifest are saved in `.mbn/` and replaced on the next run.
- RPM staging uses stable paths inside `.mbn/`.

## Options

```sh
mbn build -j 4                         # Parallel build jobs
mbn build -s rpm/example.spec          # Select a spec
mbn build --project path/to/project    # Build another project
mbn build --no-deps                    # Skip dependency installation
mbn build --define 'feature 1'         # Pass an RPM macro
mbn build --output-dir path/to/RPMS    # Choose the RPM destination
```

Builds use `rpmbuild --build-in-place -bb --noprep`. Add `--prep` only when
you want the spec's preparation stage to run in your checkout. RPM still checks
build dependencies with `--no-deps`; use it for unattended builds after installing
dependencies. Dynamic `%generate_buildrequires` installation is not supported.

## Other commands

```sh
mbn deps                              # Install build dependencies only
mbn check RPMS/example-1-1.aarch64.rpm  # Check architecture, digests and rpmlint
mbn clean                             # Remove RPM staging files
```

`clean` retains exported RPMs, logs and object files in your checkout. Use the
project's build-system clean command for a full rebuild.

Builds also run the spec's `%check` and check output RPMs. Review rpmlint output;
its failure policy follows the host configuration. These checks do not replace
Harbour validation. Output RPMs are not installed, signed or published by `mbn`.
