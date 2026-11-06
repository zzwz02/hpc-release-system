"""Diagnostic plugin to replay recorded RNG immediately before the target test."""
import os

def pytest_runtest_call(item):
    if 'TestModelTrainer::test_megnet_training' not in item.nodeid:
        return
    import torch
    path = os.environ['REPLAY_RNG_PATH']
    state = torch.load(path, map_location='cpu', weights_only=True)
    torch.set_rng_state(state['cpu_rng'])
    torch.cuda.set_rng_state(state['cuda_rng'])
    print('REPLAY_RNG', path, flush=True)
