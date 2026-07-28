"""Shared fixtures. A small simulated world is built once per session."""

from __future__ import annotations

import pytest

from bodhi.config import DataConfig
from bodhi.data.generator import generate

SMALL = DataConfig(n_accounts=1_200, n_days=45, n_devices=900, n_merchants=60,
                   mule_rate=0.03, seed=11)


@pytest.fixture(scope="session")
def sim():
    """A small but structurally complete simulation."""
    return generate(SMALL)


@pytest.fixture(scope="session")
def features(sim):
    from bodhi.features import build_account_features
    return build_account_features(sim.transactions, sim.accounts)


@pytest.fixture(scope="session")
def graph(sim):
    from bodhi.graph import build_graph
    return build_graph(sim.transactions, sim.accounts)


@pytest.fixture(scope="session")
def trained(sim):
    """A fully trained engine. Slow, so it is built once for the session."""
    from bodhi.engine import MuleHunterEngine
    engine = MuleHunterEngine().attach(
        sim.accounts, sim.transactions, sim.fraud_alerts, sim.iocs, sim.regulatory)
    engine.train(verbose=False)
    engine.score_all()
    return engine
