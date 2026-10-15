import io
import sys
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "mbn"
loader = importlib.machinery.SourceFileLoader("mbn", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
mbn = importlib.util.module_from_spec(spec)
loader.exec_module(mbn)


def result(stdout="", code=0):
    return subprocess.CompletedProcess([], code, stdout, "")


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "project with spaces"
        self.root.mkdir()
        self.uid = patch.object(mbn.os, "geteuid", return_value=1000)
        self.uid.start()
        self.addCleanup(self.uid.stop)

    def test_spec_selection(self):
        (self.root / "rpm").mkdir()
        (self.root / "rpm/a.spec").touch()
        self.assertEqual(mbn.find_spec(self.root, None).name, "a.spec")
        (self.root / "rpm/b.spec").touch()
        with self.assertRaisesRegex(mbn.Error, "Select one"):
            mbn.find_spec(self.root, None)

    def test_lock_and_clean_preserve_other_files(self):
        with mbn.workspace_lock(self.root) as state:
            with self.assertRaisesRegex(mbn.Error, "Another"):
                with mbn.workspace_lock(self.root):
                    pass
            owned = state / "run-owned"
            owned.mkdir()
            (owned / ".mbn-workspace").touch()
            (state / "packages").mkdir()
            (state / "build.log").write_text("log")
            (self.root / "main.o").write_text("object")
            foreign = state / "run-foreign"
            foreign.mkdir()
        (self.root / "RPMS").mkdir()
        self.assertEqual(mbn.main(["clean", "--project", str(self.root)]), 0)
        self.assertFalse(owned.exists())
        self.assertTrue(foreign.exists())
        self.assertTrue((self.root / "RPMS").exists())
        self.assertFalse((state / "packages").exists())
        self.assertTrue((state / "build.log").exists())
        self.assertEqual((self.root / "main.o").read_text(), "object")


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.uid = patch.object(mbn.os, "geteuid", return_value=1000)
        self.uid.start()
        self.addCleanup(self.uid.stop)

    def test_definitions_preserve_argument_boundaries(self):
        value = 'test_value hello world; $(do-not-execute)'
        self.assertEqual(mbn.definitions([value]), ["--define", value])
        for value in ("_rpmdir /tmp", "_target_cpu x86_64", "qtc_builddir /tmp", "x a\nb"):
            with self.assertRaises(mbn.Error):
                mbn.definitions([value])

    def test_literal_macro_paths(self):
        self.assertEqual(mbn.path_definition("_sourcedir", Path("/a b/rpm")),
                         ["--define", "_sourcedir /a b/rpm"])
        with self.assertRaises(mbn.Error):
            mbn.path_definition("_sourcedir", Path('/tmp/$(bad)'))

    def test_spec_parser_handles_conditionals_and_versioned_capabilities(self):
        with patch.object(mbn, "run", return_value=result("pkgconfig(Qt5Core) >= 5.6\nmake\nmake\n")) as call:
            req = mbn.build_requires(Path("/a b/rpm/a.spec"), ["--define", "feature 1"], "/a b")
        self.assertEqual(req, ["make", "pkgconfig(Qt5Core) >= 5.6"])
        self.assertIn("--buildrequires", call.call_args[0][0])
        self.assertIn("feature 1", call.call_args[0][0])

    def test_deps_no_root_or_shell_for_spec_and_exact_zypper_args(self):
        requirements = ["pkgconfig(Qt5Core) >= 5.6", "make"]
        with patch.object(mbn, "missing_dependencies", return_value=requirements[:1]), \
                patch.object(mbn, "run", return_value=result()) as call, \
                patch.object(mbn.shutil, "which", return_value="/bin/tool"), \
                patch.object(mbn.sys.stdin, "isatty", return_value=True):
            mbn.install_dependencies(requirements)
        argv = call.call_args[0][0]
        self.assertEqual(argv[:7], ["devel-su", "zypper", "--no-refresh", "install", "--no-recommends", "--", requirements[0]])
        self.assertEqual(argv[-1], "make")
        self.assertNotIn("--non-interactive", argv)

    def test_noninteractive_deps_do_not_install(self):
        with patch.object(mbn, "missing_dependencies", return_value=["missing"]), \
                patch.object(mbn, "run") as call, \
                patch.object(mbn.shutil, "which", return_value="/bin/tool"), \
                patch.object(mbn.sys.stdin, "isatty", return_value=False):
            with self.assertRaisesRegex(mbn.Error, "interactively"):
                mbn.install_dependencies(["missing"])
        call.assert_not_called()

    def test_installed_requirements_do_not_elevate(self):
        with patch.object(mbn, "missing_dependencies", return_value=[]) as check, \
                patch.object(mbn, "run") as call:
            mbn.install_dependencies(["make"])
        check.assert_called_once_with(["make"])
        call.assert_not_called()

    def test_dependency_transaction_failure_stops(self):
        with patch.object(mbn, "missing_dependencies", return_value=["missing"]), \
                patch.object(mbn, "run", side_effect=mbn.Error("refused")), \
                patch.object(mbn.shutil, "which", return_value="/bin/tool"), \
                patch.object(mbn.sys.stdin, "isatty", return_value=True):
            with self.assertRaisesRegex(mbn.Error, "refused"):
                mbn.install_dependencies(["missing"])

    def test_root_and_wrong_host_rejected(self):
        with patch.object(mbn.os, "geteuid", return_value=0):
            with self.assertRaisesRegex(mbn.Error, "normal user"):
                mbn.preflight()
        with patch.object(mbn.os, "geteuid", return_value=1000), \
                patch.object(mbn, "os_release", return_value={"ID": "debian"}), \
                patch.object(mbn.platform, "machine", return_value="x86_64"):
            with self.assertRaisesRegex(mbn.Error, "Sailfish"):
                mbn.preflight()

    def test_cross_compiler_rejected(self):
        with patch.object(mbn.os, "geteuid", return_value=1000), \
                patch.object(mbn, "os_release", return_value={"ID": "sailfishos"}), \
                patch.object(mbn.platform, "machine", return_value="aarch64"), \
                patch.object(mbn.shutil, "which", return_value="/bin/tool"), \
                patch.object(mbn, "run", side_effect=[result("x86_64-linux-gnu"), result("aarch64-linux-gnu"), result("aarch64")]):
            with self.assertRaisesRegex(mbn.Error, "must match"):
                mbn.preflight()

    def test_matching_native_architectures(self):
        for machine, compiler, arch in (
                ("aarch64", "aarch64-meego-linux-gnu", "aarch64"),
                ("armv7l", "armv7hl-meego-linux-gnueabi", "armv7hl"),
                ("i686", "i486-meego-linux-gnu", "i486"),
                ("x86_64", "x86_64-linux-gnu", "x86_64")):
            with self.subTest(machine=machine), \
                    patch.object(mbn, "os_release", return_value={"ID": "sailfishos"}), \
                    patch.object(mbn.platform, "machine", return_value=machine), \
                    patch.object(mbn.shutil, "which", return_value="/bin/tool"), \
                    patch.dict(mbn.os.environ, {}, clear=True), \
                    patch.object(mbn, "run", side_effect=[result(compiler), result(compiler),
                                      result(arch), result("--build-in-place --noprep"), result("RPM 4.19")]):
                self.assertEqual(mbn.preflight()["architecture"], arch)

    def test_architecture_families_do_not_allow_cross_width_builds(self):
        self.assertNotEqual(mbn.architecture_family("armv7hl"), mbn.architecture_family("aarch64"))
        self.assertNotEqual(mbn.architecture_family("i486"), mbn.architecture_family("x86_64"))
        self.assertIsNone(mbn.architecture_family("riscv64"))

    def test_build_uses_original_sailfish_directory_and_retains_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "sailfish"
            (project / "rpm").mkdir(parents=True)
            (project / "rpm/app.spec").write_text("Name: app\n")
            # A valid source checkout need not have its first commit yet.
            subprocess.run(["git", "init", "-q", temporary], check=True)
            calls = []

            def fail_build(argv, cwd, log):
                calls.append((argv, cwd))
                log.write_text("compiler failed")
                return 2

            with patch.object(mbn, "preflight", return_value={"architecture": "aarch64"}), \
                    patch.object(mbn, "source_root", return_value=Path(temporary)), \
                    patch.object(mbn, "build_requires", return_value=["make"]), \
                    patch.object(mbn, "rpm_inventory", return_value=["make-1-1.aarch64"]), \
                    patch.object(mbn, "install_dependencies") as deps, \
                    patch.object(mbn, "logged", side_effect=fail_build):
                with self.assertRaisesRegex(mbn.Error, "rpmbuild failed"):
                    mbn.main(["build", "--project", str(project), "--no-deps", "--define", "example value"])
            deps.assert_not_called()
            argv, cwd = calls[0]
            self.assertEqual(cwd.name, "sailfish")
            self.assertEqual(cwd, project)
            self.assertIn(str(project / "rpm/app.spec"), argv)
            self.assertIn("--build-in-place", argv)
            self.assertIn("--noprep", argv)
            self.assertNotIn("--nodeps", argv)
            self.assertNotIn("--nocheck", argv)
            self.assertNotIn("--short-circuit", argv)
            manifests = list((project / ".mbn").glob("manifest.json"))
            self.assertEqual(len(manifests), 1)
            manifest = json.loads(manifests[0].read_text())
            self.assertEqual(manifest["status"], "failed")
            self.assertEqual(manifest["build_exit"], 2)
            self.assertIsNone(manifest["git_head"])
            self.assertTrue(Path(manifest["workspace"]).exists())

    def test_success_and_lint_failure_are_recorded(self):
        for lint_exit in (0, 1):
            with self.subTest(lint_exit=lint_exit), tempfile.TemporaryDirectory() as temporary:
                project = Path(temporary)
                (project / "rpm").mkdir()
                (project / "rpm/app.spec").write_text("Name: app\n")

                def fake_logged(argv, cwd, log):
                    log.write_text("test log")
                    if argv[0] == "rpmbuild":
                        output = Path(next(a.split(" ", 1)[1] for a in argv if a.startswith("_rpmdir ")))
                        (output / "aarch64").mkdir()
                        (output / "aarch64/app-1-1.aarch64.rpm").write_bytes(b"test-rpm")
                        return 0
                    return lint_exit

                with patch.object(mbn, "preflight", return_value={"architecture": "aarch64"}), \
                        patch.object(mbn, "source_root", return_value=project), \
                        patch.object(mbn, "build_requires", return_value=[]), \
                        patch.object(mbn, "rpm_inventory", return_value=[]), \
                        patch.object(mbn, "run", side_effect=[result("aarch64"), result()]), \
                        patch.object(mbn, "logged", side_effect=fake_logged):
                    rc = mbn.main(["build", "--project", str(project), "--no-deps", "--prep"])
                self.assertEqual(rc, lint_exit)
                manifest = json.loads(next((project / ".mbn").glob("manifest.json")).read_text())
                self.assertEqual(manifest["status"], "complete" if not lint_exit else "checks-failed")
                self.assertEqual(manifest["rpms"][0]["rpmlint_exit"], lint_exit)
                self.assertEqual(len(manifest["rpms"][0]["sha256"]), 64)
                output = project / "RPMS"
                self.assertEqual([p.name for p in output.iterdir()], ["app-1-1.aarch64.rpm"])
                self.assertEqual((output / "app-1-1.aarch64.rpm").read_bytes(), b"test-rpm")
                self.assertEqual(manifest["output_directory"], str(output))
                mbn.main(["clean", "--project", str(project)])
                self.assertTrue((output / "app-1-1.aarch64.rpm").exists())
                self.assertTrue(list((project / ".mbn").glob("manifest.json")))

    def test_repeated_build_reuses_project_and_stable_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            (project / "rpm").mkdir()
            (project / "rpm/app.spec").write_text("Name: app\n")
            (project / "untracked.h").write_text("working source")
            calls = []

            def fake_logged(argv, cwd, log):
                if argv[0] != "rpmbuild":
                    return 0
                self.assertEqual(cwd, project)
                self.assertTrue((cwd / "untracked.h").exists())
                obj = cwd / "main.o"
                if calls:
                    self.assertEqual(obj.read_bytes(), b"existing object")
                    self.assertEqual(calls[0], argv)
                else:
                    obj.write_bytes(b"existing object")
                calls.append(argv)
                output = Path(next(a.split(" ", 1)[1] for a in argv if a.startswith("_rpmdir ")))
                (output / "aarch64").mkdir()
                (output / "aarch64/app.rpm").write_bytes(b"rpm")
                log.write_text("built")
                return 0

            with patch.object(mbn, "preflight", return_value={"architecture": "aarch64"}), \
                    patch.object(mbn, "source_root", return_value=project), \
                    patch.object(mbn, "build_requires", return_value=[]), \
                    patch.object(mbn, "rpm_inventory", return_value=[]), \
                    patch.object(mbn, "run", side_effect=[result("aarch64"), result()] * 2), \
                    patch.object(mbn, "logged", side_effect=fake_logged):
                for unused in range(2):
                    self.assertEqual(mbn.main(["build", "--project", str(project), "--no-deps"]), 0)
            self.assertEqual(len(calls), 2)
            self.assertFalse(list((project / ".mbn").glob("run-*")))
            self.assertTrue((project / ".mbn/build.log").exists())
            self.assertTrue((project / ".mbn/manifest.json").exists())

    def test_export_replaces_same_name_without_following_destination_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "custom-output"
            output.mkdir()
            original = root / "keep"
            original.write_bytes(b"unchanged")
            (output / "app.rpm").symlink_to(original)
            (output / "other.rpm").write_bytes(b"other")
            package = root / "app.rpm"
            package.write_bytes(b"new")
            mbn.export_rpms([package], output)
            self.assertEqual(original.read_bytes(), b"unchanged")
            self.assertEqual((output / "app.rpm").read_bytes(), b"new")
            self.assertFalse((output / "app.rpm").is_symlink())
            self.assertEqual((output / "other.rpm").read_bytes(), b"other")
            self.assertFalse(package.exists())
            self.assertEqual(sorted(p.name for p in output.iterdir()), ["app.rpm", "other.rpm"])

    def test_wrong_architecture_rpm_fails_before_lint(self):
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary) / "foreign.rpm"
            with patch.object(mbn, "run", return_value=result("x86_64")), \
                    patch.object(mbn, "logged") as lint:
                with self.assertRaisesRegex(mbn.Error, "Unexpected RPM architecture"):
                    mbn.check_rpms([package], Path(temporary), "aarch64")
            lint.assert_not_called()

    def test_live_stdout_and_stderr_are_saved_and_exit_status_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            receipt = root / "receipt"

            class LiveOutput(io.StringIO):
                def flush(self):
                    # The child cannot finish until its first line reaches stdout.
                    if "first" in self.getvalue():
                        receipt.touch()
                    super().flush()

            output = LiveOutput()
            child = """import pathlib, sys, time
print('first', flush=True)
receipt = pathlib.Path(sys.argv[1])
deadline = time.monotonic() + 5
while not receipt.exists() and time.monotonic() < deadline:
    time.sleep(0.01)
if not receipt.exists():
    sys.exit(99)
print('diagnostic', file=sys.stderr, flush=True)
sys.stdout.write('last without newline')
sys.exit(7)
"""
            with patch.object(mbn.sys, "stdout", output):
                rc = mbn.logged([sys.executable, "-c", child, str(receipt)], root, root / "build.log")
            self.assertEqual(rc, 7)
            self.assertEqual(output.getvalue(), "first\ndiagnostic\nlast without newline")
            self.assertEqual((root / "build.log").read_text(), output.getvalue())

    def test_cancel_terminates_process_group(self):
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(mbn.subprocess, "Popen") as popen, \
                patch.object(mbn.os, "killpg") as kill:
            process = popen.return_value
            process.pid = 12345
            process.wait.side_effect = [KeyboardInterrupt(), 0]
            with self.assertRaises(KeyboardInterrupt):
                mbn.logged(["rpmbuild"], temporary, Path(temporary) / "build.log")
            self.assertTrue(popen.call_args[1]["start_new_session"])
            kill.assert_called_once_with(12345, mbn.signal.SIGTERM)


@unittest.skipUnless(importlib.util.find_spec("rpm") and shutil.which("rpm"),
                     "requires native RPM tools and bindings")
class DependencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if subprocess.run(["rpm", "-q", "rpm"], stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode:
            raise unittest.SkipTest("requires an RPM-managed host")

    def test_installed_versioned_capability(self):
        self.assertEqual(mbn.missing_dependencies(["rpm >= 0"]), [])

    def test_insufficient_version_and_missing_capability(self):
        requirements = ["rpm >= 999999:1", "mbn-test-nonexistent-capability"]
        self.assertEqual(mbn.missing_dependencies(requirements), requirements)

    def test_exact_evr_and_strict_inequalities(self):
        evr = subprocess.check_output(
            ["rpm", "-q", "rpm", "--qf", "%{EPOCHNUM}:%{VERSION}-%{RELEASE}"],
            text=True)
        self.assertEqual(mbn.missing_dependencies(["rpm = " + evr]), [])
        requirements = ["rpm > " + evr, "rpm < " + evr]
        self.assertEqual(mbn.missing_dependencies(requirements), requirements)

    def test_file_and_rich_dependencies(self):
        requirements = ["/usr/bin/rpm", "(rpm or mbn-test-nonexistent-capability)"]
        self.assertEqual(mbn.missing_dependencies(requirements), [])
        missing = "(rpm and mbn-test-nonexistent-capability)"
        self.assertEqual(mbn.missing_dependencies([missing]), [missing])

    def test_invalid_requirement_fails_closed(self):
        with self.assertRaisesRegex(mbn.Error, "Invalid BuildRequires"):
            mbn.missing_dependencies(["rpm >= 1 unexpected"])


if __name__ == "__main__":
    unittest.main()
