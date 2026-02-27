#!/bin/bash

# Generic SLURM job wrapper for ProjectMetis
# Arguments follow the same convention as condor_exe.sh:
#   $1 = OUTPUTDIR
#   $2 = OUTPUTFILENAME (without .root extension in output path)
#   $3 = INPUTFILENAMES (comma-separated)
#   $4 = INDEX
#   $5 = CMSSW_VER (optional, can be empty)
#   $6 = SCRAMARCH (optional, can be empty)
#   $7+ = EXTRA_ARGS

OUTPUTDIR=$1
OUTPUTFILENAME=$2
INPUTFILENAMES=$3
INDEX=$4
CMSSW_VER=$5
SCRAMARCH=$6
shift 6
ARGS="$@"

echo "[slurm_wrapper] �始 SLURM job wrapper"
echo "[slurm_wrapper] SLURM_JOB_ID    = ${SLURM_JOB_ID}"
echo "[slurm_wrapper] SLURM_JOB_NAME  = ${SLURM_JOB_NAME}"
echo "[slurm_wrapper] SLURM_SUBMIT_DIR= ${SLURM_SUBMIT_DIR}"
echo "[slurm_wrapper] OUTPUTDIR        = ${OUTPUTDIR}"
echo "[slurm_wrapper] OUTPUTFILENAME   = ${OUTPUTFILENAME}"
echo "[slurm_wrapper] INPUTFILENAMES   = ${INPUTFILENAMES}"
echo "[slurm_wrapper] INDEX            = ${INDEX}"
echo "[slurm_wrapper] CMSSW_VER        = ${CMSSW_VER}"
echo "[slurm_wrapper] SCRAMARCH        = ${SCRAMARCH}"
echo "[slurm_wrapper] ARGS             = ${ARGS}"
echo "[slurm_wrapper] hostname         = $(hostname)"
echo "[slurm_wrapper] date             = $(date)"
echo "[slurm_wrapper] linux timestamp  = $(date +%s)"

echo "[slurm_wrapper] printing env"
printenv
echo

######################
# Set up environment #
######################

# Use SLURM_TMPDIR if available, otherwise create a local workdir
WORKDIR=${SLURM_TMPDIR:-/tmp/slurm_work_${SLURM_JOB_ID}}
mkdir -p ${WORKDIR}
cd ${WORKDIR}
echo "[slurm_wrapper] Working directory: $(pwd)"

# Copy input files from submit dir if needed
if [ -n "${SLURM_SUBMIT_DIR}" ] && [ -d "${SLURM_SUBMIT_DIR}" ]; then
    cp ${SLURM_SUBMIT_DIR}/package.tar.gz . 2>/dev/null
fi

# Untar package if present
PACKAGE=package.tar.gz
if [ -f "${PACKAGE}" ]; then
    echo "[slurm_wrapper] Untarring ${PACKAGE}"
    tar -xf ${PACKAGE}
fi

# Set up CMSSW if version specified
if [ -n "${CMSSW_VER}" ] && [ "${CMSSW_VER}" != "None" ]; then
    export SCRAM_ARCH=${SCRAMARCH}
    if [ -f /cvmfs/cms.cern.ch/cmsset_default.sh ]; then
        source /cvmfs/cms.cern.ch/cmsset_default.sh
    fi
    if [ -d "${CMSSW_VER}" ]; then
        cd ${CMSSW_VER}/src
        eval $(scramv1 runtime -sh)
        cd ${WORKDIR}
    fi
fi

####################
# Run the job      #
####################

echo "[slurm_wrapper] --- begin running ---"

# Find and run the user executable
# The executable may have been tarred up or copied alongside
USER_EXE=$(ls *.sh 2>/dev/null | grep -v "^submit_" | head -1)
if [ -n "${USER_EXE}" ] && [ -x "${USER_EXE}" ]; then
    echo "[slurm_wrapper] Running user executable: ${USER_EXE}"
    ./${USER_EXE} ${OUTPUTDIR} ${OUTPUTFILENAME} ${INPUTFILENAMES} ${INDEX} ${CMSSW_VER} ${SCRAMARCH} ${ARGS}
    RETVAL=$?
else
    echo "[slurm_wrapper] ERROR: No executable found in working directory"
    ls -la
    RETVAL=1
fi

echo "[slurm_wrapper] --- end running (exit code: ${RETVAL}) ---"

if [ "${RETVAL}" != "0" ]; then
    echo "[slurm_wrapper] Job failed with exit code ${RETVAL}"
    exit ${RETVAL}
fi

####################
# Copy output      #
####################

echo "[slurm_wrapper] --- begin copying output ---"

# Make output directory if it doesn't exist (shared filesystem)
mkdir -p ${OUTPUTDIR}

# Copy output to destination
OUTPUTFILE="${OUTPUTFILENAME}_${INDEX}.root"
if [ -f "${OUTPUTFILE}" ]; then
    echo "[slurm_wrapper] Copying ${OUTPUTFILE} to ${OUTPUTDIR}/"
    cp ${OUTPUTFILE} ${OUTPUTDIR}/
    COPY_STATUS=$?
    if [ "${COPY_STATUS}" != "0" ]; then
        echo "[slurm_wrapper] ERROR: Failed to copy output (exit code ${COPY_STATUS})"
        exit 1
    fi
elif [ -f "${OUTPUTFILENAME}.root" ]; then
    echo "[slurm_wrapper] Copying ${OUTPUTFILENAME}.root to ${OUTPUTDIR}/${OUTPUTFILE}"
    cp ${OUTPUTFILENAME}.root ${OUTPUTDIR}/${OUTPUTFILE}
    COPY_STATUS=$?
    if [ "${COPY_STATUS}" != "0" ]; then
        echo "[slurm_wrapper] ERROR: Failed to copy output (exit code ${COPY_STATUS})"
        exit 1
    fi
else
    echo "[slurm_wrapper] WARNING: No output file found"
    ls -la
fi

echo "[slurm_wrapper] --- end copying output ---"
echo "[slurm_wrapper] time at end: $(date +%s)"
echo "[slurm_wrapper] Done."
