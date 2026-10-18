#!/bin/bash
#SBATCH --job-name=forest-loss
# Account ckpt-cse is rejected on partition compute (Hyak rewrites it to compute-cse).
#SBATCH --account=ckpt-cse
#SBATCH --partition=ckpt
#SBATCH --requeue
#SBATCH --array=0-503%8
#SBATCH --cpus-per-task=1
#SBATCH --mem=12G
#SBATCH --time=08:00:00
#SBATCH --output=/gscratch/cse/bbioren/clearcut-forest-loss/logs/%A_%a.out
#SBATCH --error=/gscratch/cse/bbioren/clearcut-forest-loss/logs/%A_%a.err

set -euo pipefail
if ! type module >/dev/null 2>&1; then
  # Batch shells on Klone do not always start as login shells.
  source /etc/profile
fi
module load coenv/python/3.11.9
cd /gscratch/cse/bbioren/clearcut-forest-loss
TILE=$(.venv/bin/python pipeline/forest_loss_patches.py --tile-index "$SLURM_ARRAY_TASK_ID")
echo "task ${SLURM_ARRAY_TASK_ID} tile ${TILE}"
.venv/bin/python pipeline/forest_loss_patches.py --tile "$TILE" --out /gscratch/cse/bbioren/clearcut-forest-loss/data/global
