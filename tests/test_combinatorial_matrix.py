"""Combinatorial Matrix Test Suite: Verifies all 32 module combinations and --yolo flag propagation."""
import os
import unittest
import subprocess
import itertools

class TestCombinatorialMatrix(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.repo_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        cls.modular_launcher = os.path.join(cls.repo_dir, "bin", "agy-modular")
        cls.full_launcher = os.path.join(cls.repo_dir, "bin", "agy-full")
        assert os.path.exists(cls.modular_launcher)
        assert os.path.exists(cls.full_launcher)

    def test_all_32_modular_combinations(self):
        """Test all 2^5 = 32 combinations of modules via bin/agy-modular."""
        flags = [
            "AGY_INFRASTRUCTURE",
            "AGY_EPISTEMIC",
            "AGY_SYMBOLIC",
            "AGY_SOCRATIC",
            "AGY_SYMCOMP"
        ]

        count = 0
        for combo in itertools.product(["0", "1"], repeat=5):
            env_override = os.environ.copy()
            for flag_name, val in zip(flags, combo):
                env_override[flag_name] = val

            # Test probe without yolo
            proc = subprocess.run(
                [self.modular_launcher, "--probe"],
                env=env_override,
                capture_output=True,
                text=True
            )
            self.assertEqual(proc.returncode, 0, f"Failed for combo {combo}")
            stdout = proc.stdout
            for flag_name, val in zip(flags, combo):
                self.assertRegex(stdout, rf"{flag_name}\s*:\s*{val}")
            self.assertRegex(stdout, r"YOLO_ACTIVE\s*:\s*0")

            # Test probe with --yolo
            proc_yolo = subprocess.run(
                [self.modular_launcher, "--probe", "--yolo"],
                env=env_override,
                capture_output=True,
                text=True
            )
            self.assertEqual(proc_yolo.returncode, 0)
            self.assertRegex(proc_yolo.stdout, r"YOLO_ACTIVE\s*:\s*1")

            count += 1

        self.assertEqual(count, 32)

    def test_all_32_full_stack_combinations(self):
        """Test overriding any subset of the 5 modules on bin/agy-full."""
        flags = [
            "AGY_INFRASTRUCTURE",
            "AGY_EPISTEMIC",
            "AGY_SYMBOLIC",
            "AGY_SOCRATIC",
            "AGY_SYMCOMP"
        ]

        count = 0
        for combo in itertools.product(["0", "1"], repeat=5):
            env_override = os.environ.copy()
            for flag_name, val in zip(flags, combo):
                env_override[flag_name] = val

            proc = subprocess.run(
                [self.full_launcher, "--probe"],
                env=env_override,
                capture_output=True,
                text=True
            )
            self.assertEqual(proc.returncode, 0)
            stdout = proc.stdout
            self.assertIn(f"infra={combo[0]}", stdout)
            self.assertIn(f"epistemic={combo[1]}", stdout)
            self.assertIn(f"symbolic={combo[2]}", stdout)
            self.assertIn(f"socratic={combo[3]}", stdout)
            self.assertIn(f"symcomp={combo[4]}", stdout)
            self.assertIn("yolo=0", stdout)

            count += 1

        self.assertEqual(count, 32)

    def test_yolo_env_variable(self):
        """Test AGY_YOLO=1 environment variable activates yolo mode across all launchers."""
        env = os.environ.copy()
        env["AGY_YOLO"] = "1"
        for l_name in ["agy-full", "agy-modular", "agy-epistemic", "agy-symbolic", "agy-socratic", "agy-symcomp", "agy-infrastruktur", "agy-symbolic-epistemic"]:
            launcher = os.path.join(self.repo_dir, "bin", l_name)
            proc = subprocess.run([launcher, "--probe"], env=env, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0)
            self.assertTrue("yolo=1" in proc.stdout or "YOLO_ACTIVE        : 1" in proc.stdout)

    def test_yolo_cli_flag(self):
        """Test --yolo and -y CLI flags activate yolo mode across all launchers."""
        for flag in ["--yolo", "-y"]:
            for l_name in ["agy-full", "agy-modular", "agy-epistemic", "agy-symbolic", "agy-socratic", "agy-symcomp", "agy-infrastruktur", "agy-symbolic-epistemic"]:
                launcher = os.path.join(self.repo_dir, "bin", l_name)
                proc = subprocess.run([launcher, "--probe", flag], capture_output=True, text=True)
                self.assertEqual(proc.returncode, 0)
                self.assertTrue("yolo=1" in proc.stdout or "YOLO_ACTIVE        : 1" in proc.stdout)

if __name__ == "__main__":
    unittest.main()
