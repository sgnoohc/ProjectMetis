import os
import time

from metis.Constants import Constants
from metis.CondorTask import CondorTask
import metis.Utils as Utils

class SLURMTask(CondorTask):
    def __init__(self, **kwargs):
        """
        SLURM-based task that extends CondorTask, overriding only
        scheduler-specific methods. All IO mapping, chunking, completion
        tracking, and backup/restore logic is inherited.

        :kwarg partition: SLURM partition (default: "hpg-default")
        :kwarg qos: SLURM QoS (default: "normal")
        :kwarg account: SLURM account (default: from $SLURM_ACCOUNT env or "")
        :kwarg time: SLURM time limit (default: "08:00:00")
        :kwarg memory: SLURM memory (default: "2gb")
        :kwarg cpus_per_task: CPUs per task (default: 1)
        :kwarg gpus: GPU spec (default: None, e.g. "a100:1")
        :kwarg modules: list of module names to load (default: [])
        :kwarg slurm_submit_params: extra SBATCH directives as dict (default: {})
        :kwarg stageout_cmd: stageout command (default: None; shared filesystem uses cp)
        """
        self.partition = kwargs.get("partition", "hpg-default")
        self.qos = kwargs.get("qos", "normal")
        self.account = kwargs.get("account", os.environ.get("SLURM_ACCOUNT", ""))
        self.slurm_time = kwargs.get("time", "08:00:00")
        self.slurm_memory = kwargs.get("memory", "2gb")
        self.cpus_per_task = kwargs.get("cpus_per_task", 1)
        self.gpus = kwargs.get("gpus", None)
        self.modules = kwargs.get("modules", [])
        self.slurm_submit_params = kwargs.get("slurm_submit_params", {})
        self.stageout_cmd = kwargs.get("stageout_cmd", None)

        # Set default executable to SLURM wrapper if not provided
        if not hasattr(self, "input_executable"):
            self.input_executable = kwargs.get("executable", self.get_metis_base() + "metis/executables/slurm_exe.sh")

        # Set default output_dir for HiPerGator if not provided
        if "output_dir" not in kwargs:
            user = os.environ.get("USER", "unknown")
            group = kwargs.get("slurm_group", os.environ.get("SLURM_GROUP", ""))
            if group:
                kwargs["output_dir"] = "/blue/{0}/{1}/ProjectMetis/{2}_{3}/".format(
                    group, user,
                    kwargs.get("sample").get_datasetname().replace("/", "_").lstrip("_") if kwargs.get("sample") else "unknown",
                    kwargs.get("tag", "v0"),
                )

        # Pass all kwargs to parent CondorTask
        super(SLURMTask, self).__init__(**kwargs)

    def get_running_condor_jobs(self, extra_columns=[]):
        """
        Override to query SLURM instead of Condor.
        Returns list of dicts compatible with CondorTask expectations.
        """
        return self.get_running_slurm_jobs()

    def get_running_slurm_jobs(self):
        """
        Get list of dictionaries for SLURM jobs belonging to this task.
        Job names follow the convention: {unique_name}__{jobnum}
        """
        jobs = Utils.slurm_q(job_name_pattern="{0}__".format(self.unique_name))
        # Map fields to be compatible with CondorTask expectations
        for job in jobs:
            job["ClusterId"] = job["JobId"]
            job["JobStatus"] = job["State"]
        return jobs

    def handle_condor_job(self, this_job_dict, out, fake=False, remove_running_x_hours=48.0, remove_held_x_hours=5.0):
        """
        Override to handle SLURM job states.
        Maps SLURM states to Metis constants and auto-cancels stale jobs.
        """
        return self.handle_slurm_job(this_job_dict, out, fake=fake,
                                      remove_running_x_hours=remove_running_x_hours,
                                      remove_held_x_hours=remove_held_x_hours)

    def handle_slurm_job(self, this_job_dict, out, fake=False, remove_running_x_hours=48.0, remove_held_x_hours=5.0):
        """
        Takes `out` (File object) and dictionary of SLURM job info.
        Returns action_type string specifying the action taken.
        """
        job_id = str(this_job_dict.get("JobId", this_job_dict.get("ClusterId", "?")))
        status = this_job_dict.get("State", this_job_dict.get("JobStatus", "I"))
        hours_since = abs(time.time() - int(this_job_dict.get("EnteredCurrentStatus", time.time()))) / 3600.

        action_type = "UNKNOWN"
        out.set_status(Constants.RUNNING)

        running = status == "R"
        idle = status == "I"
        held = status == "H"

        if running:
            self.logger.debug("SLURM job {0} for ({1}) running for {2:.1f} hrs".format(job_id, out, hours_since))
            action_type = "RUNNING"
            out.set_status(Constants.RUNNING)

            if hours_since > remove_running_x_hours:
                self.logger.debug("SLURM job {0} for ({1}) cancelled for running too long".format(job_id, out))
                if not fake:
                    Utils.slurm_rm([job_id])
                action_type = "LONG_RUNNING_REMOVED"

        elif idle:
            self.logger.debug("SLURM job {0} for ({1}) pending for {2:.1f} hrs".format(job_id, out, hours_since))
            action_type = "IDLE"
            out.set_status(Constants.IDLE)

        elif held:
            raw_state = this_job_dict.get("RawState", "UNKNOWN")
            reason = this_job_dict.get("Reason", "???")
            self.logger.debug("SLURM job {0} for ({1}) in state {2} for {3:.1f} hrs: {4}".format(
                job_id, out, raw_state, hours_since, reason))
            action_type = "HELD"
            out.set_status(Constants.HELD)

            if hours_since > remove_held_x_hours:
                self.logger.info("SLURM job {0} for ({1}) cancelled due to failed/held state".format(job_id, out))
                if not fake:
                    Utils.slurm_rm([job_id])
                action_type = "HELD_AND_REMOVED"

        return action_type

    def submit_multiple_condor_jobs(self, v_ins, v_out, fake=False, optimizer=None):
        """
        Override to submit via SLURM instead of Condor.
        Submits one sbatch job per output.
        Returns (succeeded:bool, first_job_id:str).
        """
        return self.submit_multiple_slurm_jobs(v_ins, v_out, fake=fake, optimizer=optimizer)

    def submit_multiple_slurm_jobs(self, v_ins, v_out, fake=False, optimizer=None):
        """
        Submit multiple SLURM jobs, one per output file.
        Returns (succeeded:bool, comma_separated_job_ids:str).
        """
        outdir = self.output_dir
        outname_noext = self.output_name.rsplit(".", 1)[0]
        v_inputs_commasep = [",".join(map(lambda x: x.get_name(), ins)) for ins in v_ins]
        v_index = [out.get_index() for out in v_out]
        cmssw_ver = self.cmssw_version
        scramarch = self.scram_arch
        executable = self.executable_path
        logdir_full = os.path.abspath("{0}/logs/".format(self.get_taskdir()))
        package_full = os.path.abspath(self.package_path) if self.tarfile else None
        input_files = [package_full] if self.tarfile else []
        input_files.append(os.path.abspath(executable))
        input_files += self.additional_input_files

        all_succeeded = True
        job_ids = []

        for index, inputs_commasep, out in zip(v_index, v_inputs_commasep, v_out):
            job_name = "{0}__{1}".format(self.unique_name, index)
            arguments = [outdir, outname_noext, inputs_commasep,
                         index, cmssw_ver, scramarch, self.arguments]

            extra_directives = dict(self.slurm_submit_params)

            succeeded, job_id = Utils.slurm_submit(
                executable=executable,
                arguments=arguments,
                inputfiles=input_files,
                logdir=logdir_full,
                job_name=job_name,
                partition=self.partition,
                qos=self.qos,
                account=self.account,
                time=self.slurm_time,
                memory=self.slurm_memory,
                cpus_per_task=self.cpus_per_task,
                gpus=self.gpus,
                modules=self.modules,
                extra_directives=extra_directives,
                stageout_cmd=self.stageout_cmd,
                fake=fake,
            )

            if succeeded:
                job_ids.append(str(job_id))
                if index not in self.job_submission_history:
                    self.job_submission_history[index] = []
                self.job_submission_history[index].append(str(job_id))
                ntimes = len(self.job_submission_history[index])
                if ntimes <= 1:
                    self.logger.info("SLURM job for ({0}) submitted as {1}".format(out, job_id))
                else:
                    self.logger.info("SLURM job for ({0}) submitted as {1} (for the {2} time)".format(
                        out, job_id, Utils.num_to_ordinal_string(ntimes)))
            else:
                all_succeeded = False

        combined_id = ",".join(job_ids) if job_ids else "-1"
        return all_succeeded, combined_id

    def run(self, fake=False, optimizer=None):
        """
        Main logic for looping through (inputs,output) pairs.
        Uses SLURM for job management instead of Condor.
        """
        slurm_job_dicts = self.get_running_slurm_jobs()
        slurm_job_indices = set([int(rj["jobnum"]) for rj in slurm_job_dicts if rj["jobnum"] >= 0])

        nfiles_reset = self.recache_outputs()
        if nfiles_reset > 0:
            self.logger.info("{0} files may have been deleted".format(nfiles_reset))

        to_submit = []

        for iout, (ins, out) in enumerate(self.io_mapping):
            if self.max_jobs > 0 and iout >= self.max_jobs:
                break

            index = out.get_index()
            on_slurm = index in slurm_job_indices
            done = (out.exists() and not on_slurm)
            if done:
                self.handle_done_output(out)
                continue

            if fake:
                out.set_fake()

            if not on_slurm:
                to_submit.append({
                    "ins": ins,
                    "out": out,
                })
            else:
                this_job_dict = next(rj for rj in slurm_job_dicts if int(rj["jobnum"]) == index)
                self.handle_slurm_job(this_job_dict, out)

        if to_submit:
            v_ins = [d["ins"] for d in to_submit]
            v_out = [d["out"] for d in to_submit]
            self.submit_multiple_slurm_jobs(v_ins, v_out, fake=fake, optimizer=optimizer)

    def try_to_complete(self):
        """
        Override to use scancel instead of condor_rm for tail jobs.
        """
        if self.min_completion_fraction > 1. - 1.e-3:
            return
        if not self.complete():
            return

        for sjob in self.get_running_slurm_jobs():
            job_id = sjob["JobId"]
            Utils.slurm_rm([job_id])
            self.logger.info("Tail SLURM job {} cancelled".format(job_id))
        files_to_remove = [output.get_name() for output in self.get_uncompleted_outputs()]
        new_mapping = []
        for ins, out in self.get_io_mapping():
            if out in files_to_remove:
                continue
            new_mapping.append([ins, out])
        for fname in files_to_remove:
            Utils.do_cmd("rm {}".format(fname))
            self.logger.info("Tail root file {} removed".format(fname))
        self.io_mapping = new_mapping

    def get_task_summary(self):
        """
        Returns a dictionary with mapping and SLURM job info/history.
        Mirrors CondorTask.get_task_summary() but uses SLURM job IDs and log paths.
        """
        logdir_full = os.path.abspath("{0}/logs/std_logs/".format(self.get_taskdir())) + "/"

        d_onslurm = {}
        for job in self.get_running_slurm_jobs():
            d_onslurm[job["JobId"]] = job

        d_history = self.get_job_submission_history()

        d_jobs = {}
        for ins, out in self.get_io_mapping():
            index = out.get_index()
            d_jobs[index] = {}
            d_jobs[index]["output"] = [out.get_name(), out.get_nevents()]
            d_jobs[index]["output_exists"] = out.exists()
            d_jobs[index]["inputs"] = list(map(lambda x: [x.get_name(), x.get_nevents()], ins))
            submission_history = d_history.get(index, [])
            is_on_condor = False
            last_job_id = -1
            if len(submission_history) > 0:
                last_job_id = submission_history[-1]
                is_on_condor = last_job_id in d_onslurm
            d_jobs[index]["current_job"] = d_onslurm.get(last_job_id, {})
            d_jobs[index]["is_on_condor"] = is_on_condor
            d_jobs[index]["condor_jobs"] = []
            for job_id in submission_history:
                d_job = {
                    "cluster_id": job_id,
                    "logfile_err": "{0}/slurm_{1}.err".format(logdir_full, job_id),
                    "logfile_out": "{0}/slurm_{1}.out".format(logdir_full, job_id),
                }
                d_jobs[index]["condor_jobs"].append(d_job)

        d_summary = {
            "jobs": d_jobs,
            "queried_nevents": (self.queried_nevents if not self.open_dataset else self.sample.get_nevents()),
            "open_dataset": self.open_dataset,
            "output_dir": self.output_dir,
            "tag": self.tag,
            "global_tag": self.global_tag,
            "cmssw_version": self.cmssw_version,
            "timestamp": Utils.get_timestamp(),
            "executable": self.input_executable,
            "task_type": self.get_task_name(),
            "taskdir": os.path.abspath(self.get_taskdir()),
            "scheduler": "slurm",
            "partition": self.partition,
            "qos": self.qos,
        }

        d_summary = self.supplement_task_summary(d_summary)
        return d_summary


if __name__ == "__main__":
    pass
