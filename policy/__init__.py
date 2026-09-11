"""Datasets, models, training and export.

``dataset.py`` (the training samples of CLAUDE.md 5.3 over recorded sessions), ``diffusion.py`` (the
Diffusion Policy of 5.7, wrapped so it takes the goal channels and the task one-hot, plus the
``runtime.policy_api.Policy`` adapter), ``train.py`` (the training entry point) and ``export.py``
(checkpoint -> inference bundle). See docs/policy.md.

Nothing in this package can move the robot: it reads recorded sessions, returns tensors, and writes
checkpoints. Only ``runtime/controller.py`` sends, and only through ``runtime/safety.py`` (R1, R3).
"""
