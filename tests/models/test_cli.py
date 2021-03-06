import os
import pandas as pd
import pytest
import sklearn
import sklearn.datasets
import sklearn.neighbors
import subprocess
import sys

try:
    from StringIO import StringIO
except ImportError:
    from io import StringIO

import mlflow
from mlflow import pyfunc
import mlflow.sklearn
from mlflow.utils.file_utils import TempDir, path_to_local_file_uri
from mlflow.utils.environment import _mlflow_conda_env
from mlflow.utils import PYTHON_VERSION
from tests.models import test_pyfunc

in_travis = 'TRAVIS' in os.environ
# NB: for now, windows tests on Travis do not have conda available.
no_conda = ["--no-conda"] if in_travis and sys.platform == "win32" else []

# NB: need to install mlflow since the pip version does not have mlflow models cli.
install_mlflow = ["--install-mlflow"] if not no_conda else []

extra_options = no_conda + install_mlflow


@pytest.fixture(scope="module")
def iris_data():
    iris = sklearn.datasets.load_iris()
    x = iris.data[:, :2]
    y = iris.target
    return x, y


@pytest.fixture(scope="module")
def sk_model(iris_data):
    x, y = iris_data
    knn_model = sklearn.neighbors.KNeighborsClassifier()
    knn_model.fit(x, y)
    return knn_model


def test_predict_with_old_mlflow_in_conda_and_with_orient_records(iris_data):
    if no_conda:
        pytest.skip("This test needs conda.")
    # TODO: Enable this test after 1.0 is out to ensure we do not break the serve / predict
    # TODO: Also add a test for serve, not just predict.
    pytest.skip("TODO: enable this after 1.0 release is out.")
    x, _ = iris_data
    with TempDir() as tmp:
        input_records_path = tmp.path("input_records.json")
        pd.DataFrame(x).to_json(input_records_path, orient="records")
        output_json_path = tmp.path("output.json")
        test_model_path = tmp.path("test_model")
        test_model_conda_path = tmp.path("conda.yml")
        # create env with odl mlflow!
        _mlflow_conda_env(path=test_model_conda_path,
                          additional_pip_deps=["mlflow=={}".format(test_pyfunc.MLFLOW_VERSION)])
        pyfunc.save_model(path=test_model_path,
                          loader_module=test_pyfunc.__name__.split(".")[-1],
                          code_path=[test_pyfunc.__file__],
                          conda_env=test_model_conda_path)
        # explicit json format with orient records
        p = subprocess.Popen(["mlflow", "models", "predict", "-m",
                              path_to_local_file_uri(test_model_path), "-i", input_records_path,
                              "-o", output_json_path, "-t", "json", "--json-format", "records"]
                             + no_conda)
        assert 0 == p.wait()
        actual = pd.read_json(output_json_path, orient="records")
        actual = actual[actual.columns[0]].values
        expected = test_pyfunc.PyFuncTestModel(check_version=False).predict(df=pd.DataFrame(x))
        assert all(expected == actual)


def test_mlflow_is_not_installed_unless_specified():
    if no_conda:
        pytest.skip("This test requires conda.")
    with TempDir(chdr=True) as tmp:
        fake_model_path = tmp.path("fake_model")
        fake_env_path = tmp.path("fake_env.yaml")
        _mlflow_conda_env(path=fake_env_path, install_mlflow=False)
        mlflow.pyfunc.save_model(fake_model_path, loader_module=__name__, conda_env=fake_env_path)
        # The following should fail because there should be no mlflow in the env:
        p = subprocess.Popen(["mlflow", "models", "predict", "-m", fake_model_path],
                             stderr=subprocess.PIPE, cwd=tmp.path(""))
        _, stderr = p.communicate()
        stderr = stderr.decode("utf-8")
        print(stderr)
        assert p.wait() != 0
        if PYTHON_VERSION.startswith("3"):
            assert "ModuleNotFoundError: No module named 'mlflow'" in stderr
        else:
            assert "ImportError: No module named mlflow.pyfunc.scoring_server" in stderr


def test_model_with_no_deployable_flavors_fails_pollitely():
    from mlflow.models import Model
    with TempDir(chdr=True) as tmp:
        m = Model(artifact_path=None, run_id=None, utc_time_created="now",
                  flavors={"some": {}, "useless": {}, "flavors": {}})
        os.mkdir(tmp.path("model"))
        m.save(tmp.path("model", "MLmodel"))
        # The following should fail because there should be no suitable flavor
        p = subprocess.Popen(["mlflow", "models", "predict", "-m", tmp.path("model")],
                             stderr=subprocess.PIPE, cwd=tmp.path(""))
        _, stderr = p.communicate()
        stderr = stderr.decode("utf-8")
        print(stderr)
        assert p.wait() != 0
        assert "No suitable flavor backend was found for the model." in stderr


def test_predict(iris_data, sk_model):
    with TempDir(chdr=True) as tmp:
        with mlflow.start_run() as active_run:
            mlflow.sklearn.log_model(sk_model, "model")
            model_uri = "runs:/{run_id}/model".format(run_id=active_run.info.run_id)
        input_json_path = tmp.path("input.json")
        input_csv_path = tmp.path("input.csv")
        output_json_path = tmp.path("output.json")
        x, _ = iris_data
        pd.DataFrame(x).to_json(input_json_path, orient="split")
        pd.DataFrame(x).to_csv(input_csv_path, index=False)

        # Test with no conda
        p = subprocess.Popen(["mlflow", "models", "predict", "-m", model_uri, "-i", input_json_path,
                              "-o", output_json_path, "--no-conda"], stderr=subprocess.PIPE)
        assert p.wait() == 0
        actual = pd.read_json(output_json_path, orient="records")
        actual = actual[actual.columns[0]].values
        expected = sk_model.predict(x)
        assert all(expected == actual)

        # With conda + --install-mlflow
        p = subprocess.Popen(["mlflow", "models", "predict", "-m", model_uri, "-i", input_json_path,
                              "-o", output_json_path] + extra_options)
        assert 0 == p.wait()
        actual = pd.read_json(output_json_path, orient="records")
        actual = actual[actual.columns[0]].values
        expected = sk_model.predict(x)
        assert all(expected == actual)

        # explicit json format with default orient (should be split)
        p = subprocess.Popen(["mlflow", "models", "predict", "-m", model_uri, "-i", input_json_path,
                              "-o", output_json_path, "-t", "json"] + extra_options)
        assert 0 == p.wait()
        actual = pd.read_json(output_json_path, orient="records")
        actual = actual[actual.columns[0]].values
        expected = sk_model.predict(x)
        assert all(expected == actual)

        # explicit json format with orient==split
        p = subprocess.Popen(["mlflow", "models", "predict", "-m", model_uri, "-i", input_json_path,
                              "-o", output_json_path, "-t", "json", "--json-format", "split"]
                             + extra_options)
        assert 0 == p.wait()
        actual = pd.read_json(output_json_path, orient="records")
        actual = actual[actual.columns[0]].values
        expected = sk_model.predict(x)
        assert all(expected == actual)

        # read from stdin, write to stdout.
        p = subprocess.Popen(["mlflow", "models", "predict", "-m", model_uri, "-t", "json",
                              "--json-format", "split"] + extra_options,
                             universal_newlines=True,
                             stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE,
                             stderr=sys.stderr)
        with open(input_json_path, "r") as f:
            stdout, _ = p.communicate(f.read())
        assert 0 == p.wait()
        actual = pd.read_json(StringIO(stdout), orient="records")
        actual = actual[actual.columns[0]].values
        expected = sk_model.predict(x)
        assert all(expected == actual)

        # NB: We do not test orient=records here because records may loose column ordering.
        # orient == records is tested in other test with simpler model.

        # csv
        p = subprocess.Popen(["mlflow", "models", "predict", "-m", model_uri, "-i", input_csv_path,
                              "-o", output_json_path, "-t", "csv"] + extra_options)
        assert 0 == p.wait()
        actual = pd.read_json(output_json_path, orient="records")
        actual = actual[actual.columns[0]].values
        expected = sk_model.predict(x)
        assert all(expected == actual)
