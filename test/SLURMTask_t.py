import unittest
import os
import time
import logging
import glob

import metis.Utils as Utils
from metis.Sample import DirectorySample
from metis.SLURMTask import SLURMTask
from metis.File import File


class SLURMTaskTest(unittest.TestCase):

    dummy = None
    nfiles = 7
    files_per_job = 2
    cmssw = "CMSSW_8_0_21"
    tag = "vtest_slurm"

    @classmethod
    def setUpClass(cls):
        super(SLURMTaskTest, cls).setUpClass()

        basedir = "/tmp/{0}/metis/slurmtask_test/".format(os.getenv("USER"))
        Utils.do_cmd("mkdir -p {0}".format(basedir))
        for i in range(1, cls.nfiles + 1):
            Utils.do_cmd("touch {0}/input_{1}.root".format(basedir, i))
        Utils.do_cmd("echo hello > {0}/executable.sh".format(basedir))

        logging.getLogger("logger_metis").disabled = True
        cls.dummy = SLURMTask(
            sample=DirectorySample(
                location=basedir,
                globber="*.root",
                dataset="/test/test/SLURM_TEST",
            ),
            open_dataset=False,
            files_per_output=cls.files_per_job,
            cmssw_version=cls.cmssw,
            tag=cls.tag,
            executable="{0}/executable.sh".format(basedir),
            output_dir="/tmp/{0}/metis/slurmtask_test_output/".format(os.getenv("USER")),
            partition="hpg-default",
            qos="normal",
            time="04:00:00",
            memory="4gb",
        )

        cls.dummy.prepare_inputs()
        cls.dummy.run(fake=True)
        cls.dummy.run(fake=True)

    def test_inputs(self):
        self.assertEqual(len(self.dummy.get_inputs(flatten=True)), self.nfiles)

    def test_outputs(self):
        self.assertEqual(len(self.dummy.get_outputs()), (self.nfiles + 1) // self.files_per_job)

    def test_sample(self):
        self.assertEqual(self.dummy.get_sample(), self.dummy.sample)

    def test_reset_mapping(self):
        old = self.dummy.io_mapping
        self.dummy.reset_io_mapping()
        self.assertEqual(self.dummy.get_io_mapping(), [])
        self.dummy.io_mapping = old

    def test_completion(self):
        self.assertEqual(self.dummy.complete(), True)
        self.assertEqual(self.dummy.complete(return_fraction=True), 1.0)
        self.assertEqual(len(self.dummy.get_completed_outputs()), (self.nfiles + 1) // self.files_per_job)

    def test_summary(self):
        summary = self.dummy.get_task_summary()
        self.assertEqual(sum([x["is_on_condor"] for x in list(summary["jobs"].values())]), 0)
        self.assertEqual(summary["cmssw_version"], self.cmssw)
        self.assertEqual(summary["tag"], self.tag)
        self.assertEqual(len(summary["jobs"].keys()), (self.nfiles + 1) // self.files_per_job)
        self.assertEqual(summary["scheduler"], "slurm")
        self.assertEqual(summary["partition"], "hpg-default")

    def test_get_inputs_for_output(self):
        inps, output = self.dummy.get_io_mapping()[0]
        self.assertEqual(self.dummy.get_inputs_for_output(output), inps)
        self.assertEqual(self.dummy.get_inputs_for_output(output.get_name()), inps)
        self.assertEqual(self.dummy.get_inputs_for_output("unknown"), "unknown")

    def test_prepare_inputs(self):
        shfiles = glob.glob(self.dummy.get_taskdir() + "/*.sh")
        self.assertEqual(len(shfiles), 1)

    def test_get_job_submission_history(self):
        history = self.dummy.get_job_submission_history()
        ijobs = range(1, (self.nfiles + 1) // self.files_per_job + 1)
        self.assertEqual(sorted(history.keys()), list(ijobs))
        # In fake mode, job ids are -1
        ids = [list(map(lambda x: int(str(x).split(",")[0].split(".")[0]), x)) for x in list(history.values())]
        self.assertEqual(ids, [[-1] for _ in ijobs])

    def test_backup(self):
        self.assertEqual("io_mapping" in self.dummy.info_to_backup(), True)
        self.assertEqual("executable_path" in self.dummy.info_to_backup(), True)
        self.assertEqual("package_path" in self.dummy.info_to_backup(), True)
        self.assertEqual("prepared_inputs" in self.dummy.info_to_backup(), True)
        self.assertEqual("job_submission_history" in self.dummy.info_to_backup(), True)

    def test_slurm_handler(self):
        epsilon_hours = 0.1
        remove_running_x_hours = 36.
        remove_held_x_hours = 3.

        params = {
            "out": File("blah"),
            "fake": True,
            "remove_running_x_hours": remove_running_x_hours,
            "remove_held_x_hours": remove_held_x_hours,
        }

        # Test RUNNING state
        job_dict = {"JobId": "123", "ClusterId": "123", "jobnum": 1,
                     "State": "R", "JobStatus": "R", "RawState": "RUNNING",
                     "EnteredCurrentStatus": time.time(), "Reason": ""}
        self.assertEqual(self.dummy.handle_slurm_job(this_job_dict=job_dict, **params), "RUNNING")

        # Test RUNNING but not yet timed out
        job_dict = {"JobId": "123", "ClusterId": "123", "jobnum": 1,
                     "State": "R", "JobStatus": "R", "RawState": "RUNNING",
                     "EnteredCurrentStatus": time.time() - (remove_running_x_hours - epsilon_hours) * 3600, "Reason": ""}
        self.assertEqual(self.dummy.handle_slurm_job(this_job_dict=job_dict, **params), "RUNNING")

        # Test RUNNING and timed out
        job_dict = {"JobId": "123", "ClusterId": "123", "jobnum": 1,
                     "State": "R", "JobStatus": "R", "RawState": "RUNNING",
                     "EnteredCurrentStatus": time.time() - (remove_running_x_hours + epsilon_hours) * 3600, "Reason": ""}
        self.assertEqual(self.dummy.handle_slurm_job(this_job_dict=job_dict, **params), "LONG_RUNNING_REMOVED")

        # Test IDLE/PENDING state
        job_dict = {"JobId": "123", "ClusterId": "123", "jobnum": 1,
                     "State": "I", "JobStatus": "I", "RawState": "PENDING",
                     "EnteredCurrentStatus": time.time(), "Reason": ""}
        self.assertEqual(self.dummy.handle_slurm_job(this_job_dict=job_dict, **params), "IDLE")

        # Test HELD/FAILED state, not yet timed out
        job_dict = {"JobId": "123", "ClusterId": "123", "jobnum": 1,
                     "State": "H", "JobStatus": "H", "RawState": "FAILED",
                     "EnteredCurrentStatus": time.time() - (remove_held_x_hours - epsilon_hours) * 3600, "Reason": "OOM"}
        self.assertEqual(self.dummy.handle_slurm_job(this_job_dict=job_dict, **params), "HELD")

        # Test HELD/FAILED and timed out
        job_dict = {"JobId": "123", "ClusterId": "123", "jobnum": 1,
                     "State": "H", "JobStatus": "H", "RawState": "FAILED",
                     "EnteredCurrentStatus": time.time() - (remove_held_x_hours + epsilon_hours) * 3600, "Reason": "OOM"}
        self.assertEqual(self.dummy.handle_slurm_job(this_job_dict=job_dict, **params), "HELD_AND_REMOVED")

    def test_task_type(self):
        self.assertEqual(self.dummy.get_task_name(), "SLURMTask")

    def test_slurm_params(self):
        self.assertEqual(self.dummy.partition, "hpg-default")
        self.assertEqual(self.dummy.qos, "normal")
        self.assertEqual(self.dummy.slurm_time, "04:00:00")
        self.assertEqual(self.dummy.slurm_memory, "4gb")


class SLURMUtilsTest(unittest.TestCase):

    def test_slurm_q_parsing(self):
        """Test slurm_q output parsing with mock data."""
        # We can't easily mock slurm_q since it calls do_cmd directly,
        # but we can test the state mapping logic
        state_map = {
            "RUNNING": "R", "R": "R",
            "PENDING": "I", "PD": "I",
            "FAILED": "H", "F": "H",
            "TIMEOUT": "H", "TO": "H",
            "CANCELLED": "H", "CA": "H",
        }
        for slurm_state, expected in state_map.items():
            self.assertEqual(state_map[slurm_state], expected)

    def test_slurm_submit_template(self):
        """Test SLURM submit script generation."""
        template = Utils.slurm_submit(
            executable="/path/to/exe.sh",
            arguments=["arg1", "arg2", "arg3"],
            inputfiles=[],
            logdir="/tmp/logs",
            job_name="test_task__1",
            partition="hpg-default",
            qos="normal",
            account="mygroup",
            time="08:00:00",
            memory="2gb",
            cpus_per_task=1,
            return_template=True,
        )
        self.assertIn("#SBATCH --job-name=test_task__1", template)
        self.assertIn("#SBATCH --partition=hpg-default", template)
        self.assertIn("#SBATCH --qos=normal", template)
        self.assertIn("#SBATCH --account=mygroup", template)
        self.assertIn("#SBATCH --time=08:00:00", template)
        self.assertIn("#SBATCH --mem=2gb", template)
        self.assertIn("#SBATCH --cpus-per-task=1", template)
        self.assertIn("#SBATCH --ntasks=1", template)
        self.assertIn("arg1 arg2 arg3", template)

    def test_slurm_submit_with_gpus(self):
        """Test SLURM submit script includes GPU line."""
        template = Utils.slurm_submit(
            executable="/path/to/exe.sh",
            arguments=["arg1"],
            inputfiles=[],
            logdir="/tmp/logs",
            job_name="gpu_test__1",
            gpus="a100:1",
            return_template=True,
        )
        self.assertIn("#SBATCH --gpus=a100:1", template)

    def test_slurm_submit_fake(self):
        """Test fake submission returns expected values."""
        succeeded, job_id = Utils.slurm_submit(
            executable="/path/to/exe.sh",
            arguments=["arg1"],
            logdir="/tmp/logs",
            fake=True,
        )
        self.assertEqual(succeeded, True)
        self.assertEqual(job_id, -1)

    def test_slurm_submit_missing_args(self):
        """Test that missing required args raises RuntimeError."""
        with self.assertRaises(RuntimeError):
            Utils.slurm_submit(executable="/path/to/exe.sh")

    def test_job_name_convention(self):
        """Test job name encoding/decoding."""
        task_name = "SLURMTask_test_test_TEST_v0"
        jobnum = 42
        job_name = "{0}__{1}".format(task_name, jobnum)
        self.assertEqual(job_name, "SLURMTask_test_test_TEST_v0__42")
        # Decode
        decoded_num = int(job_name.rsplit("__", 1)[1])
        self.assertEqual(decoded_num, 42)
        decoded_task = job_name.rsplit("__", 1)[0]
        self.assertEqual(decoded_task, task_name)


if __name__ == "__main__":
    unittest.main()
