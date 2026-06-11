import os
import logging
from concurrent.futures import ThreadPoolExecutor

import metis.Utils as Utils


class PackedSLURMSubmitter(object):
    """
    Collects pending work items across multiple SLURMTask objects,
    groups them into packs of `pack_size`, and submits one SLURM job
    per pack using slurm_submit_packed().

    Each sub-job within a pack runs independently and produces its own
    output file. Completion is tracked per output file by the parent
    SLURMTask, so failed sub-jobs are naturally retried on the next round
    without re-running successful ones.
    """

    def __init__(self, pack_size=32, cpus_per_subjob=1, packed_executable=None, **slurm_kwargs):
        """
        :param pack_size: max sub-jobs per SLURM allocation
        :param cpus_per_subjob: CPUs allocated to each sub-job (controls NPARALLEL in slurm_packed_executable.sh)
        :param packed_executable: path to slurm_packed_executable.sh
        :param slurm_kwargs: SLURM directives passed through to slurm_submit_packed
            (partition, qos, account, time, memory, gpus, modules, extra_directives)
        """
        self.pack_size = pack_size
        self.cpus_per_subjob = cpus_per_subjob
        self.packed_executable = packed_executable
        self.slurm_kwargs = slurm_kwargs
        self.logger = logging.getLogger(self.__class__.__name__)

    def process(self, tasks, fake=False, max_submitted=None):
        """
        Main entry point. Collects pending items across all tasks,
        packs them, and submits.

        :param tasks: list of SLURMTask objects
        :param fake: if True, don't actually submit
        :param max_submitted: max total SLURM jobs to have in queue (None = unlimited)
        """
        # Prepare inputs for tasks that haven't been prepared yet
        ntasks = len(tasks)
        n_to_prepare = sum(1 for t in tasks if (not t.prepared_inputs) or t.recopy_inputs)
        if n_to_prepare > 0:
            print("  [packed] Preparing inputs for {0}/{1} tasks ...".format(n_to_prepare, ntasks))
        for i, task in enumerate(tasks):
            if (not task.prepared_inputs) or task.recopy_inputs:
                print("\r  [packed] Preparing {0}/{1}: {2}".format(i + 1, ntasks, task.unique_name[:60]), end="", flush=True)
                task.prepare_inputs()
        if n_to_prepare > 0:
            print()

        # Query squeue once and reuse for all tasks
        print("  [packed] Querying squeue ...", flush=True)
        cached_all_jobs = Utils.slurm_q()

        # Collect pending items across all tasks (threaded — each task does NFS listdir)
        pending = []
        print("  [packed] Collecting pending items from {0} tasks (threaded) ...".format(ntasks))
        done_count = [0]  # mutable for closure

        def _scan_task(task):
            items = task.get_pending_items(cached_all_jobs=cached_all_jobs)
            done_count[0] += 1
            print("\r  [packed] Scanning {0}/{1}".format(done_count[0], ntasks), end="", flush=True)
            return items

        with ThreadPoolExecutor(max_workers=16) as executor:
            results = executor.map(_scan_task, tasks)
            for items in results:
                pending.extend(items)
        print()

        if pending:
            print("  [packed] {0} pending sub-jobs across {1} tasks".format(
                len(pending), len(tasks)))
            self.logger.info("Collected {0} pending sub-jobs across {1} tasks".format(
                len(pending), len(tasks)))

            # Group into packs and submit
            submitted = 0
            skipped = 0
            # Check queue once before submission loop (avoid repeated squeue calls)
            if max_submitted is not None:
                current_queued = len(cached_all_jobs)
                print("  [packed] Current queue: {0} jobs (limit {1})".format(current_queued, max_submitted))
            for i in range(0, len(pending), self.pack_size):
                if max_submitted is not None and (current_queued + submitted) >= max_submitted:
                    skipped = len(range(i, len(pending), self.pack_size))
                    print("  [packed] Queue limit reached ({0} + {1} >= {2}), skipping {3} remaining pack(s)".format(
                        current_queued, submitted, max_submitted, skipped))
                    self.logger.info("Queue limit reached, stopping submission")
                    break

                pack = pending[i:i + self.pack_size]
                succeeded = self._submit_pack(pack, fake=fake)
                if succeeded:
                    submitted += 1

            if submitted > 0:
                self.logger.info("Submitted {0} packed SLURM job(s)".format(submitted))

        # Handle completion / backup for all tasks (threaded)
        print("  [packed] Finalizing {0} tasks (threaded) ...".format(ntasks))
        done_final = [0]

        def _finalize_task(task):
            task.try_to_complete()
            if task.complete():
                task.finalize()
            task.backup()
            done_final[0] += 1
            print("\r  [packed] Finalizing {0}/{1}".format(done_final[0], ntasks), end="", flush=True)

        with ThreadPoolExecutor(max_workers=16) as executor:
            list(executor.map(_finalize_task, tasks))
        print()

    def _submit_pack(self, pack, fake=False):
        """
        Write manifest and submit one packed SLURM job.

        :param pack: list of dicts with keys: ins, out, task
        :param fake: if True, don't actually submit
        :returns: True if submission succeeded
        """
        if not pack:
            return False

        # Use first task's logdir for manifest storage
        first_task = pack[0]["task"]
        logdir = os.path.abspath("{0}/logs/".format(first_task.get_taskdir()))

        # Build pack_items for slurm_submit_packed
        pack_items = []
        for item in pack:
            task = item["task"]
            out = item["out"]
            ins = item["ins"]
            pack_items.append({
                "task_name": task.unique_name,
                "index": out.get_index(),
                "arguments": [
                    task.output_dir,
                    task.output_name.rsplit(".", 1)[0],
                    ",".join(f.get_name() for f in ins),
                    out.get_index(),
                    task.cmssw_version,
                    task.scram_arch,
                    task.arguments,
                ],
            })

        # Collect shared input files from first task (all tasks share the same package)
        inputfiles = []
        if first_task.tarfile:
            inputfiles.append(os.path.abspath(first_task.package_path))
        inputfiles.append(os.path.abspath(self.packed_executable))
        inputfiles += first_task.additional_input_files

        # Compute resources
        cpus_total = len(pack) * self.cpus_per_subjob

        print("  [packed] Submitting: {0} sub-jobs, {1} CPUs total".format(
            len(pack), cpus_total))
        self.logger.info("Submitting packed job: {0} sub-jobs, {1} CPUs total".format(
            len(pack), cpus_total))

        try:
            succeeded, job_id = Utils.slurm_submit_packed(
                pack_items=pack_items,
                executable=self.packed_executable,
                inputfiles=inputfiles,
                logdir=logdir,
                cpus_per_task=cpus_total,
                cpus_per_subjob=self.cpus_per_subjob,
                fake=fake,
                **self.slurm_kwargs,
            )
        except RuntimeError as e:
            self.logger.error("Packed submission failed: {0}".format(e))
            return False

        if succeeded:
            # Print job ID and sub-job list to stdout
            task_indices = ["{0}[{1}]".format(item["task"].unique_name, item["out"].get_index()) for item in pack]
            print("  [packed] Job {0} submitted: {1}".format(job_id, ", ".join(task_indices)))
            print("  [packed] Log: tail -f {0}/std_logs/slurm_{1}.out".format(logdir, job_id))

            # Record submission in each task's history
            for item in pack:
                task = item["task"]
                index = item["out"].get_index()
                if index not in task.job_submission_history:
                    task.job_submission_history[index] = []
                task.job_submission_history[index].append(str(job_id))
                self.logger.info("Packed job {0}: {1} index {2}".format(
                    job_id, task.unique_name, index))

            # Copy manifest and symlink logs to all tasks' log directories
            src_manifest = os.path.join(logdir, "packed_{0}.manifest".format(job_id))
            src_out = os.path.join(logdir, "std_logs", "slurm_{0}.out".format(job_id))
            src_err = os.path.join(logdir, "std_logs", "slurm_{0}.err".format(job_id))
            for item in pack:
                task = item["task"]
                dest_logdir = os.path.abspath("{0}/logs/".format(task.get_taskdir()))
                if dest_logdir != logdir:
                    try:
                        import shutil
                        Utils.do_cmd("mkdir -p {0}/std_logs".format(dest_logdir))
                        # Copy manifest
                        if os.path.exists(src_manifest):
                            dest_manifest = os.path.join(dest_logdir, "packed_{0}.manifest".format(job_id))
                            if not os.path.exists(dest_manifest):
                                shutil.copy2(src_manifest, dest_manifest)
                        # Symlink log files so each task can find them
                        for src_log in [src_out, src_err]:
                            dest_log = os.path.join(dest_logdir, "std_logs", os.path.basename(src_log))
                            if not os.path.exists(dest_log):
                                os.symlink(src_log, dest_log)
                    except Exception:
                        pass

        return succeeded
