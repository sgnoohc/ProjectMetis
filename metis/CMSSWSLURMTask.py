import os
import json

from metis.SLURMTask import SLURMTask
from metis.Constants import Constants
import metis.Utils as Utils
import traceback

class CMSSWSLURMTask(SLURMTask):
    def __init__(self, **kwargs):
        """
        CMSSW task for SLURM. Extends SLURMTask the same way
        CMSSWTask extends CondorTask.

        :kwarg pset: CMSSW Python config file path
        :kwarg pset_args: extra arguments to pass to cmsRun
        :kwarg check_expectedevents: validate output event count
        :kwarg is_data: is this data (vs MC)?
        :kwarg is_tree_output: is output a tree (vs histogram)?
        :kwarg dont_check_tree: skip ROOT tree validation
        :kwarg dont_edit_pset: don't modify pset automatically
        :kwarg other_outputs: additional output files to copy back
        :kwarg report_every: MessageLogger reporting frequency
        """
        self.pset = kwargs.get("pset", None)
        self.pset_args = kwargs.get("pset_args", "print")
        self.check_expectedevents = kwargs.get("check_expectedevents", True)
        self.is_data = kwargs.get("is_data", False)
        self.input_executable = kwargs.get("executable", self.get_metis_base() + "metis/executables/slurm_cmssw_exe.sh")
        self.other_outputs = kwargs.get("other_outputs", [])
        self.output_is_tree = kwargs.get("is_tree_output", True)
        self.dont_check_tree = kwargs.get("dont_check_tree", False)
        self.dont_edit_pset = kwargs.get("dont_edit_pset", False)
        self.report_every = kwargs.get("report_every", 1000)

        super(CMSSWSLURMTask, self).__init__(**kwargs)

        if not self.read_only:
            if not self.global_tag:
                self.global_tag = self.sample.get_globaltag()

    def info_to_backup(self):
        return ["io_mapping", "executable_path", "pset_path",
                "package_path", "prepared_inputs",
                "job_submission_history", "global_tag", "queried_nevents"]

    def handle_done_output(self, out):
        out.set_status(Constants.DONE)
        self.logger.debug("This output ({0}) exists, skipping the processing".format(out))
        if not self.is_data and self.output_is_tree:
            self.logger.debug("Calculating negative events for this file")
            try:
                out.get_nevents_negative()
            except Exception as e:
                self.logger.info("{}\nSomething wrong with this file. Delete it by hand. {}{}".format(
                    "-"*50, traceback.format_exc(), "-"*50,
                ))

    def finalize(self):
        d_metadata = self.get_legacy_metadata()
        self.write_metadata(d_metadata)

    def submit_multiple_slurm_jobs(self, v_ins, v_out, fake=False, optimizer=None):
        """
        Extended SLURM submission with CMSSW-specific arguments.
        """
        outdir = self.output_dir
        outname_noext = self.output_name.rsplit(".", 1)[0]
        v_inputs_commasep = [",".join(map(lambda x: x.get_name(), ins)) for ins in v_ins]
        v_index = [out.get_index() for out in v_out]
        pset_full = os.path.abspath(self.pset_path)
        pset_basename = os.path.basename(self.pset_path)
        cmssw_ver = self.cmssw_version
        scramarch = self.scram_arch
        max_nevents_per_job = self.kwargs.get("max_nevents_per_job", -1)
        nevts = max_nevents_per_job
        v_firstevt = [-1 for out in v_out]
        v_expectedevents = [-1 for out in v_out]
        if self.check_expectedevents:
            v_expectedevents = [out.get_nevents() for out in v_out]
            if max_nevents_per_job > 0:
                v_expectedevents = [max_nevents_per_job for out in v_out]

        if self.split_within_files:
            nevts = self.events_per_output
            v_firstevt = [1 + (out.get_index() - 1) * (self.events_per_output + 1) for out in v_out]
            v_expectedevents = [-1 for out in v_out]
            v_inputs_commasep = ["dummyfile" for ins in v_ins]
        pset_args = self.pset_args
        executable = self.executable_path
        other_outputs = ",".join(self.other_outputs) or "None"
        logdir_full = os.path.abspath("{0}/logs/".format(self.get_taskdir()))
        package_full = os.path.abspath(self.package_path)
        input_files = [package_full, pset_full] if self.tarfile else [pset_full]
        input_files.append(os.path.abspath(executable))
        input_files += self.additional_input_files

        all_succeeded = True
        job_ids = []

        for index, inputs_commasep, out, firstevt, expectedevents in zip(
                v_index, v_inputs_commasep, v_out, v_firstevt, v_expectedevents):
            job_name = "{0}__{1}".format(self.unique_name, index)
            arguments = [outdir, outname_noext, inputs_commasep,
                         index, pset_basename, cmssw_ver, scramarch,
                         nevts, firstevt, expectedevents, other_outputs, pset_args]

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

    def prepare_inputs(self):
        self.executable_path = "{0}/executable.sh".format(self.get_taskdir())
        self.package_path = "{0}/package.tar.gz".format(self.get_taskdir())
        self.pset_path = "{0}/pset.py".format(self.get_taskdir())

        if not os.path.exists(self.input_executable):
            to_check = os.path.join(self.get_metis_base(), self.input_executable)
            if os.path.exists(to_check):
                self.input_executable = to_check

        Utils.do_cmd("cp {0} {1}".format(self.input_executable, self.executable_path))

        pset_location_in = self.pset
        pset_location_out = self.pset_path
        with open(pset_location_in, "r") as fhin:
            data_in = fhin.read()
        with open(pset_location_out, "w") as fhin:
            fhin.write(data_in)
            if not self.dont_edit_pset:
                fhin.write("""
if hasattr(process,"eventMaker"):
    process.eventMaker.CMS3tag = cms.string('{tag}')
    process.eventMaker.datasetName = cms.string('{dsname}')
    process.out.dropMetaData = cms.untracked.string("NONE")
    if hasattr(process,"GlobalTag"):
        process.GlobalTag.globaltag = "{gtag}"
if hasattr(process,"MessageLogger"):
    process.MessageLogger.cerr.FwkReport.reportEvery = {reportevery}

def set_output_name(outputname):
    to_change = []
    for attr in dir(process):
        if not hasattr(process,attr): continue
        if (type(getattr(process,attr)) != cms.OutputModule) and (attr not in ["TFileService"]): continue
        to_change.append([process,attr])
    for i in range(len(to_change)):
        getattr(to_change[i][0],to_change[i][1]).fileName = outputname
\n\n""".format(tag=self.tag, dsname=self.get_sample().get_datasetname(), gtag=self.global_tag, reportevery=self.report_every)
                )

            if self.sparms:
                sparms = ['"{0}"'.format(sparm) for sparm in self.sparms]
                fhin.write("\nprocess.sParmMaker.vsparms = cms.untracked.vstring(\n{0}\n)\n\n".format(",\n".join(sparms)))

        if self.split_within_files:
            fnames = ['"{0}"'.format(fo.get_name()) for fo in self.get_inputs(flatten=True)]
            fnames = sorted(list(set(fnames)))
            with open(pset_location_out, "a") as fhin:
                fhin.write("\nif hasattr(process.source,\"fileNames\"): process.source.fileNames = cms.untracked.vstring([\n{0}\n][:255])\n\n".format(",\n".join(fnames)))
                fhin.write("\nif hasattr(process,\"RandomNumberGeneratorService\"): process.RandomNumberGeneratorService.generator.initialSeed = cms.untracked.uint32(int(__import__('random').getrandbits(28)))\n\n")
                fhin.write("\nif hasattr(process,\"RandomNumberGeneratorService\"): process.RandomNumberGeneratorService.externalLHEProducer.initialSeed = cms.untracked.uint32(int(__import__('random').getrandbits(17)))\n\n")

        if self.tarfile:
            Utils.do_cmd("cp {0} {1}".format(self.tarfile, self.package_path))

        self.prepared_inputs = True

    def get_legacy_metadata(self):
        d_metadata = {}
        d_metadata["ijob_to_miniaod"] = {}
        d_metadata["ijob_to_nevents"] = {}
        done_nevents = 0
        for ins, out in self.get_io_mapping():
            if out.get_status() != Constants.DONE:
                continue
            d_metadata["ijob_to_miniaod"][out.get_index()] = list(map(lambda x: x.get_name(), ins))
            nevents = out.get_nevents()
            nevents_pos = out.get_nevents_positive() if self.output_is_tree else 0
            nevents_eff = nevents_pos - (nevents - nevents_pos)
            d_metadata["ijob_to_nevents"][out.get_index()] = [nevents, nevents_eff]
            done_nevents += out.get_nevents()
        d_metadata["basedir"] = os.path.abspath(self.get_basedir())
        d_metadata["taskdir"] = os.path.abspath(self.get_taskdir())
        d_metadata["tag"] = self.tag
        d_metadata["dataset"] = self.get_sample().get_datasetname()
        d_metadata["gtag"] = self.global_tag
        d_metadata["pset"] = self.pset
        d_metadata["pset_args"] = self.pset_args
        d_metadata["cmsswver"] = self.cmssw_version
        d_metadata["nevents_DAS"] = done_nevents if not self.open_dataset else self.get_sample().get_nevents()
        d_metadata["nevents_merged"] = done_nevents
        d_metadata["finaldir"] = self.get_outputdir()
        d_metadata["efact"] = self.sample.info["efact"]
        d_metadata["kfact"] = self.sample.info["kfact"]
        d_metadata["xsec"] = self.sample.info["xsec"]
        d_metadata["scheduler"] = "slurm"
        return d_metadata

    def write_metadata(self, d_metadata):
        metadata_file = d_metadata["finaldir"] + "/metadata.json"
        with open(metadata_file, "w") as fhout:
            json.dump(d_metadata, fhout, sort_keys=True, indent=4)
        Utils.do_cmd("cp {0}/backup.pkl {1}/".format(self.get_taskdir(), d_metadata["finaldir"]))
        self.logger.info("Dumped metadata and backup pickle")

    def supplement_task_summary(self, task_summary):
        task_summary["pset"] = self.pset
        task_summary["pset_args"] = self.pset_args
        return task_summary


if __name__ == "__main__":
    pass
