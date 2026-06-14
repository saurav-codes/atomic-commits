from atomic_commits.config import RunConfig
from atomic_commits.selftest import run_selftest


def test_selftest_broad_profile_commits_generated_repo(tmp_path):
    summary = run_selftest(
        work_dir=tmp_path / "selftest",
        cfg_template=RunConfig(),
        cases=1,
        seed=42,
        profile="broad",
    )

    assert summary.cases_run == 1
    assert summary.failures == []
