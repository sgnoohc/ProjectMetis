#!/bin/bash

# CMSSW SLURM job wrapper for ProjectMetis
# Arguments follow the same convention as condor_cmssw_exe.sh:
#   $1  = OUTPUTDIR
#   $2  = OUTPUTNAME (without .root)
#   $3  = INPUTFILENAMES (comma-separated)
#   $4  = IFILE (job index)
#   $5  = PSET (pset.py basename)
#   $6  = CMSSWVERSION
#   $7  = SCRAMARCH
#   $8  = NEVTS
#   $9  = FIRSTEVT
#   $10 = EXPECTEDNEVTS
#   $11 = OTHEROUTPUTS (comma-separated)
#   $12+= PSETARGS

OUTPUTDIR=$1
OUTPUTNAME=$2
INPUTFILENAMES=$3
IFILE=$4
PSET=$5
CMSSWVERSION=$6
SCRAMARCH=$7
NEVTS=$8
FIRSTEVT=$9
EXPECTEDNEVTS=${10}
OTHEROUTPUTS=${11}
PSETARGS="${@:12}"

# Make sure OUTPUTNAME doesn't have .root since we add it manually
OUTPUTNAME=$(echo $OUTPUTNAME | sed 's/\.root//')

export SCRAM_ARCH=${SCRAMARCH}

function write_status {
    # Write status to a file instead of condor_chirp
    STATUS_FILE="${SLURM_SUBMIT_DIR:-.}/slurm_status_${SLURM_JOB_ID}.txt"
    echo "$1 = $2" >> ${STATUS_FILE} 2>/dev/null
    echo "[status] $1 => $2"
}

function edit_pset {
    echo "process.maxEvents.input = cms.untracked.int32(${NEVTS})" >> pset.py
    echo "if hasattr(process,'externalLHEProducer'):" >> pset.py
    echo "    process.externalLHEProducer.nEvents = cms.untracked.uint32(${NEVTS})" >> pset.py
    echo "set_output_name(\"${OUTPUTNAME}.root\")" >> pset.py
    if [[ "$INPUTFILENAMES" != "dummy"* ]]; then
        echo "process.source.fileNames = cms.untracked.vstring([" >> pset.py
        for INPUTFILENAME in $(echo "$INPUTFILENAMES" | sed -n 1'p' | tr ',' '\n'); do
            echo "\"${INPUTFILENAME}\"," >> pset.py
        done
        echo "])" >> pset.py
    fi
    if [ "$FIRSTEVT" -ge 0 ]; then
        echo "try:" >> pset.py
        echo "    if not 'Empty' in str(process.source): process.source.skipEvents = cms.untracked.uint32(max(${FIRSTEVT}-1,0))" >> pset.py
        echo "except: pass" >> pset.py
        echo "try:" >> pset.py
        echo "    process.source.firstEvent = cms.untracked.uint32(${FIRSTEVT})" >> pset.py
        echo "except: pass" >> pset.py
    fi
}

function stageout {
    COPY_SRC=$1
    COPY_DEST=$2
    retries=0
    COPY_STATUS=1
    until [ $retries -ge 3 ]
    do
        echo "Stageout attempt $((retries+1)): cp ${COPY_SRC} ${COPY_DEST}"
        mkdir -p $(dirname ${COPY_DEST})
        cp ${COPY_SRC} ${COPY_DEST}
        COPY_STATUS=$?
        if [ $COPY_STATUS -ne 0 ]; then
            echo "Failed stageout attempt $((retries+1))"
        else
            echo "Successful stageout with $retries retries"
            break
        fi
        retries=$[$retries+1]
        echo "Sleeping for 30s"
        sleep 30
    done
    if [ $COPY_STATUS -ne 0 ]; then
        echo "Removing output file because copy failed with code $COPY_STATUS"
        rm -f ${COPY_DEST}
    fi
    return $COPY_STATUS
}

echo -e "\n--- begin header output ---\n"
echo "OUTPUTDIR: $OUTPUTDIR"
echo "OUTPUTNAME: $OUTPUTNAME"
echo "INPUTFILENAMES: $INPUTFILENAMES"
echo "IFILE: $IFILE"
echo "PSET: $PSET"
echo "CMSSWVERSION: $CMSSWVERSION"
echo "SCRAMARCH: $SCRAMARCH"
echo "NEVTS: $NEVTS"
echo "EXPECTEDNEVTS: $EXPECTEDNEVTS"
echo "OTHEROUTPUTS: $OTHEROUTPUTS"
echo "PSETARGS: $PSETARGS"

echo "SLURM_JOB_ID: $SLURM_JOB_ID"
echo "SLURM_JOB_NAME: $SLURM_JOB_NAME"
echo "hostname: $(hostname)"
echo "uname -a: $(uname -a)"
echo "time: $(date +%s)"
echo "args: $@"
echo -e "\n--- end header output ---\n"

# Source CMS environment
if [ -r "$OSGVO_CMSSW_Path"/cmsset_default.sh ]; then
    echo "sourcing environment: source $OSGVO_CMSSW_Path/cmsset_default.sh"
    source "$OSGVO_CMSSW_Path"/cmsset_default.sh
elif [ -r "$OSG_APP"/cmssoft/cms/cmsset_default.sh ]; then
    echo "sourcing environment: source $OSG_APP/cmssoft/cms/cmsset_default.sh"
    source "$OSG_APP"/cmssoft/cms/cmsset_default.sh
elif [ -r /cvmfs/cms.cern.ch/cmsset_default.sh ]; then
    echo "sourcing environment: source /cvmfs/cms.cern.ch/cmsset_default.sh"
    source /cvmfs/cms.cern.ch/cmsset_default.sh
else
    echo "ERROR! Couldn't find cmsset_default.sh"
    exit 1
fi

# Handle tarball: full CMSSW or selective
tarfile=package.tar.gz
if [ -f ${tarfile} ]; then
    if [ ! -z $(tar -tf ${tarfile} | head -n 1 | grep "^CMSSW") ]; then
        echo "this is a full cmssw tar file"
        tar xf ${tarfile}
        cd $CMSSWVERSION
        echo $PWD
        echo "Running ProjectRename"
        scramv1 b ProjectRename
        echo "Running scramv1 runtime -sh"
        eval $(scramv1 runtime -sh)
        mv ../$PSET pset.py
        mv ../${tarfile} .
    else
        echo "this is a selective cmssw tar file"
        eval $(scramv1 project CMSSW $CMSSWVERSION)
        cd $CMSSWVERSION
        eval $(scramv1 runtime -sh)
        mv ../$PSET pset.py
        if [ -e ../${tarfile} ]; then
            mv ../${tarfile} ${tarfile}
            tar xf ${tarfile}
        fi
        scram b
        [ -e package.tar.gz ] && tar xf package.tar.gz
    fi
else
    # No tarball, just set up CMSSW
    eval $(scramv1 project CMSSW $CMSSWVERSION)
    cd $CMSSWVERSION
    eval $(scramv1 runtime -sh)
    if [ -e ../$PSET ]; then
        mv ../$PSET pset.py
    fi
fi

echo "before running: ls -lrth"
ls -lrth

echo -e "\n--- begin running ---\n"

write_status ChirpMetisExpectedNevents $EXPECTEDNEVTS
write_status ChirpMetisStatus "before_cmsRun"

edit_pset

cmsRun pset.py ${PSETARGS}
CMSRUN_STATUS=$?

write_status ChirpMetisStatus "after_cmsRun"

echo "after running: ls -lrth"
ls -lrth

if [[ $CMSRUN_STATUS != 0 ]]; then
    echo "Removing output file because cmsRun crashed with exit code $CMSRUN_STATUS"
    rm -f ${OUTPUTNAME}.root
    exit 1
fi

# Validate output (sweeproot check)
python << EOL
import ROOT as r
import os
import traceback
foundBad = False
try:
    f1 = r.TFile("${OUTPUTNAME}.root")
    t = f1.Get("Events")
    nevts = t.GetEntries()
    expectednevts = ${EXPECTEDNEVTS}
    print("[RSR] ntuple has %i events and expected %i" % (t.GetEntries(), expectednevts))
    if int(expectednevts) > 0 and int(t.GetEntries()) != int(expectednevts):
        print("[RSR] nevents mismatch")
        foundBad = True
    for i in range(0, t.GetEntries(), 1):
        if t.GetEntry(i) < 0:
            foundBad = True
            print("[RSR] found bad event %i" % i)
            break
except Exception as ex:
    msg = traceback.format_exc()
    if "EDProductGetter" not in msg:
        foundBad = True
if foundBad:
    print("[RSR] removing output file because it does not deserve to live")
    os.system("rm ${OUTPUTNAME}.root")
else:
    print("[RSR] passed the rigorous sweeproot")
EOL

if [ "$?" != "0" ]; then
    echo "Removing output file because sweeproot crashed with exit code $?"
    rm -f ${OUTPUTNAME}.root
    exit 1
fi

echo -e "\n--- end running ---\n"

echo -e "\n--- begin copying output ---\n"

echo "Sending output file ${OUTPUTNAME}.root"

if [ ! -e "${OUTPUTNAME}.root" ]; then
    echo "ERROR! Output ${OUTPUTNAME}.root doesn't exist"
    exit 1
fi

echo "time before copy: $(date +%s)"
write_status ChirpMetisStatus "before_copy"

# Stageout to shared filesystem (default for HiPerGator)
stageout "${OUTPUTNAME}.root" "${OUTPUTDIR}/${OUTPUTNAME}_${IFILE}.root"

for OTHEROUTPUT in $(echo "$OTHEROUTPUTS" | sed -n 1'p' | tr ',' '\n'); do
    if [ "$OTHEROUTPUT" == "None" ]; then continue; fi
    [ -e ${OTHEROUTPUT} ] && {
        NOROOT=$(echo $OTHEROUTPUT | sed 's/\.root//')
        stageout "${NOROOT}.root" "${OUTPUTDIR}/${NOROOT}_${IFILE}.root"
    }
done

echo -e "\n--- end copying output ---\n"

echo "time at end: $(date +%s)"
write_status ChirpMetisStatus "done"
