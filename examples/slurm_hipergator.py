"""
Example: Submitting jobs to HiPerGator (SLURM) using ProjectMetis

This example shows how to use SLURMTask for submitting generic jobs
to the University of Florida's HiPerGator cluster.

Before running:
  - Set SLURM_ACCOUNT environment variable (or pass account= kwarg)
  - Ensure input files exist at the specified location
  - Ensure output directory is writable (e.g., /blue/<group>/<user>/...)

Usage:
  python slurm_hipergator.py
"""

import time
import os

from metis.SLURMTask import SLURMTask
from metis.Sample import DirectorySample

# --- Configuration ---
user = os.environ.get("USER", "unknown")
group = os.environ.get("SLURM_GROUP", "mygroup")  # your HiPerGator group

# Create a sample from a directory of input files
sample = DirectorySample(
    location="/blue/{0}/{1}/inputs/".format(group, user),
    globber="*.root",
    dataset="/MyData/v1/RAW",
)

# Create the SLURM task
task = SLURMTask(
    sample=sample,
    files_per_output=2,
    executable="./my_script.sh",
    output_name="output.root",
    tag="v0",
    output_dir="/blue/{0}/{1}/outputs/MyData_v0/".format(group, user),

    # SLURM-specific options
    partition="hpg-default",
    qos="normal",
    account=os.environ.get("SLURM_ACCOUNT", group),
    time="04:00:00",
    memory="4gb",
    cpus_per_task=1,
    # gpus="a100:1",  # uncomment for GPU jobs

    # Optional: modules to load on the worker node
    # modules=["gcc/12.2.0", "root/6.28"],

    # Optional: extra SBATCH directives
    # slurm_submit_params={"mail-type": "END", "mail-user": "you@ufl.edu"},
)

# --- Processing loop ---
while not task.complete():
    task.process()

    # Print progress
    frac = task.complete(return_fraction=True)
    print("Progress: {0:.1%} complete".format(frac))

    if not task.complete():
        print("Sleeping for 5 minutes...")
        time.sleep(300)

print("All done!")
