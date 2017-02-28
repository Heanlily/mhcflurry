import tempfile
import shutil
import os

from numpy.testing import assert_allclose
from nose.tools import eq_

from mhcflurry.class1_allele_specific import scoring
from mhcflurry.class1_allele_specific_ensemble.measurement_collection import (
    MeasurementCollection)
from mhcflurry.dataset import Dataset
from mhcflurry.downloads import get_path


from mhcflurry \
    .class1_allele_specific_ensemble \
    .class1_ensemble_multi_allele_predictor import (
        Class1EnsembleMultiAllelePredictor,
        HYPERPARAMETER_DEFAULTS)


def test_basic():
    model_hyperparameters = HYPERPARAMETER_DEFAULTS.models_grid(
        impute=[False, True],
        activation=["tanh"],
        layer_sizes=[[4], [16]],
        embedding_output_dim=[16],
        dropout_probability=[.25],
        n_training_epochs=[20])
    model = Class1EnsembleMultiAllelePredictor(
        ensemble_size=3,
        hyperparameters_to_search=model_hyperparameters)
    print(model)

    dataset = Dataset.from_csv(get_path(
        "data_combined_iedb_kim2014", "combined_human_class1_dataset.csv"))
    sub_dataset = Dataset(
        dataset._df.ix[
            (dataset._df.allele.isin(["HLA-A0101", "HLA-A0205"])) &
            (dataset._df.peptide.str.len() == 9)
        ])

    mc = MeasurementCollection.from_dataset(sub_dataset)
    print(model.description())
    print("Now fitting.")
    model.fit(mc)
    print(model.description())
    ic50_pred = model.predict(mc)
    ic50_true = mc.df.measurement_value

    scores = scoring.make_scores(ic50_true, ic50_pred)
    print(scores)
    assert scores['auc'] > 0.85, "Expected higher AUC"

    # test save and restore
    try:
        tmpdir = tempfile.mkdtemp(prefix="mhcflurry-test")
        model.write_fit(
            os.path.join(tmpdir, "models.csv"),
            tmpdir)
        model2 = Class1EnsembleMultiAllelePredictor.load_fit(
            os.path.join(tmpdir, "models.csv"),
            tmpdir)
    finally:
        shutil.rmtree(tmpdir)

    eq_(model.ensemble_size, model2.ensemble_size)
    eq_(model.supported_alleles(), model2.supported_alleles())
    eq_(model.hyperparameters_to_search, model2.hyperparameters_to_search)
    ic50_pred2 = model.predict(mc)
    assert_allclose(ic50_pred, ic50_pred2)
