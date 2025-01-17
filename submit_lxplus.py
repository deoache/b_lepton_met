import os
import argparse
import subprocess
from pathlib import Path
from wprime_plus_b.utils.load_config import load_dataset_config
from utils import (
    get_command,
    run_checker,
    build_filesets,
    manage_processor_args,
    build_output_directories,
)


def move_X509() -> str:
    """move x509 proxy file from /tmp to /afs/private. Returns the afs path"""
    try:
        x509_localpath = (
            [
                line
                for line in os.popen("voms-proxy-info").read().split("\n")
                if line.startswith("path")
            ][0]
            .split(":")[-1]
            .strip()
        )
    except Exception as err:
        raise RuntimeError(
            "x509 proxy could not be parsed, try creating it with 'voms-proxy-init --voms cms'"
        ) from err
    x509_path = f"{Path.home()}/private/{x509_localpath.split('/')[-1]}"
    subprocess.run(["cp", x509_localpath, x509_path])
    return x509_path


def get_jobpath(args: dict) -> str:
    path = args["processor"]
    if args["channel"]:
        path += f'/{args["channel"]}'
    if args["lepton_flavor"]:
        path += f'/{args["lepton_flavor"]}'
    path += f'/{args["year"]}'
    path += f'/{args["sample"]}'
    return path


def get_jobname(args: dict) -> str:
    jobname = args["processor"]
    if args["channel"]:
        jobname += f'_{args["channel"]}'
    if args["lepton_flavor"]:
        jobname += f'_{args["lepton_flavor"]}'
    jobname += f'_{args["sample"]}'
    if args["nsample"]:
        jobname += f'_{args["nsample"]}'
    return jobname


def submit_condor(args: dict, cmd: str, flavor: str, submit: bool) -> None:
    """build condor and executable files, and submit condor job"""
    main_dir = Path.cwd()
    condor_dir = Path(f"{main_dir}/condor")

    # set path and jobname
    jobpath = get_jobpath(args)
    jobname = get_jobname(args)
    print(f"creating job for {jobname}")

    # create logs and condor directories
    log_dir = Path(f"{str(condor_dir)}/logs/{jobpath}")
    if not log_dir.exists():
        log_dir.mkdir(parents=True)
    local_condor_path = Path(f"{condor_dir}/{jobpath}/")
    if not local_condor_path.exists():
        local_condor_path.mkdir(parents=True)
    local_condor = f"{local_condor_path}/{jobname}.sub"

    # make condor file
    condor_template_file = open(f"{condor_dir}/submit.sub")
    condor_file = open(local_condor, "w")
    for line in condor_template_file:
        line = line.replace("DIRECTORY", str(condor_dir))
        line = line.replace("JOBPATH", jobpath)
        line = line.replace("JOBNAME", jobname)
        line = line.replace("PROCESSOR", args["processor"])
        line = line.replace("YEAR", args["year"])
        line = line.replace("JOBFLAVOR", f'"{flavor}"')
        condor_file.write(line)
    condor_file.close()
    condor_template_file.close()

    # make executable file
    x509_path = move_X509()
    sh_template_file = open(f"{condor_dir}/submit.sh")
    local_sh = f"{local_condor_path}/{jobname}.sh"
    sh_file = open(local_sh, "w")
    for line in sh_template_file:
        line = line.replace("MAINDIRECTORY", str(main_dir))
        line = line.replace("COMMAND", cmd)
        line = line.replace("X509PATH", x509_path)
        sh_file.write(line)
    sh_file.close()
    sh_template_file.close()

    # submit jobs
    if submit:
        print(f"submitting to condor")
        subprocess.run(["condor_submit", local_condor])


def main(args):
    args = manage_processor_args(vars(args))
    submit = eval(args["submit"])
    del args["submit"]
    run_checker(args)
    # add facility and output path to args
    args["facility"] = "lxplus"
    args["output_path"] = build_output_directories(args)
    # build filesets
    root_files_list = build_filesets(args)
    del args["nroots"]
    # get dataset config
    dataset_config = load_dataset_config(config_name=args["sample"])
    # run job for each partition
    if len(root_files_list) == 1:
        cmd = get_command(args)
        submit_condor(args, cmd, flavor="longlunch", submit=submit)
    else:
        for nsplit in range(1, len(root_files_list) + 1):
            args["nsample"] = nsplit
            cmd = get_command(args)
            submit_condor(args, cmd, flavor="workday", submit=submit)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--processor",
        dest="processor",
        type=str,
        default="",
        help="processor to be used {ttbar, ztoll, qcd, trigger_eff, btag_eff} (default ttbar)",
    )
    parser.add_argument(
        "--channel",
        dest="channel",
        type=str,
        default="",
        help="channel to be processed",
    )
    parser.add_argument(
        "--lepton_flavor",
        dest="lepton_flavor",
        type=str,
        default="",
        help="lepton flavor to be processed {'mu', 'ele'}",
    )
    parser.add_argument(
        "--sample",
        dest="sample",
        type=str,
        default="",
        help="sample key to be processed",
    )
    parser.add_argument(
        "--year",
        dest="year",
        type=str,
        default="",
        help="year of the data {2016APV, 2016, 2017, 2018} (default 2017)",
    )
    parser.add_argument(
        "--executor",
        dest="executor",
        type=str,
        default="",
        help="executor to be used {iterative, futures, dask} (default iterative)",
    )
    parser.add_argument(
        "--workers",
        dest="workers",
        type=int,
        default=4,
        help="number of workers to use with futures executor (default 4)",
    )
    parser.add_argument(
        "--nfiles",
        dest="nfiles",
        type=int,
        default=1,
        help="number of .root files to be processed by sample. To run all files use -1 (default 1)",
    )
    parser.add_argument(
        "--output_type",
        dest="output_type",
        type=str,
        default="",
        help="type of output {hist, array}",
    )
    parser.add_argument(
        "--syst",
        dest="syst",
        type=str,
        default="nominal",
        help="systematic to apply {'nominal', 'jet', 'met', 'full'}",
    )
    parser.add_argument(
        "--nsample",
        dest="nsample",
        type=str,
        default="",
        help="partitions to run (--nsample 1,2,3 will only run partitions 1,2 and 3)",
    )
    parser.add_argument(
        "--submit",
        dest="submit",
        type=str,
        default="True",
        help="if True submit job to Condor. If False, it just builds datasets and condor files",
    )
    parser.add_argument(
        "--flow",
        dest="flow",
        type=str,
        default="True",
        help="whether to include underflow/overflow to first/last bin {True, False}",
    )
    parser.add_argument(
        "--nroots",
        dest="nroots",
        type=int,
        default=10,
        help="number of root files for the datasets splits",
    )
    args = parser.parse_args()
    main(args)
