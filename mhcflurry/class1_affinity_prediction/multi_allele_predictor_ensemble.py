import collections
import pickle
import time
import hashlib
from os.path import join, exists

import numpy
import pandas

import mhcnames

from ..encodable_sequences import EncodableSequences

from .class1_binding_predictor import Class1BindingPredictor


class MultiAllelePredictorEnsemble(object):
    def __init__(
            self,
            allele_to_allele_specific_models={},
            class1_pan_allele_models=[],
            allele_to_pseudosequence=None,
            manifest_df=None):

        if class1_pan_allele_models:
            assert allele_to_pseudosequence, "Pseudosequences required"

        self.allele_to_allele_specific_models = (
            allele_to_allele_specific_models)
        self.class1_pan_allele_models = class1_pan_allele_models
        self.allele_to_pseudosequence = allele_to_pseudosequence

        if manifest_df is None:
            manifest_df = pandas.DataFrame()
            manifest_df["name"] = []
            manifest_df["allele"] = []
            manifest_df["hyperparameters"] = []
            manifest_df["history"] = []
            manifest_df["num_measurements"] = []
            manifest_df["random_negative_rate"] = []
            manifest_df["sources"] = []
            manifest_df["fit_seconds"] = []
            manifest_df["model"] = []
        self.manifest_df = manifest_df

    def save(self, models_dir, model_names_to_write=None):
        num_models = len(self.class1_pan_allele_models) + sum(
            len(v) for v in self.allele_to_allele_specific_models.values())
        assert len(self.manifest_df) == num_models, (
            "Manifest seems out of sync with models: %d vs %d entries" % (
                len(self.manifest_df), num_models))

        if model_names_to_write is None:
            # Write all models
            models_names_to_write = self.manifest_df.model_name.values

        sub_manifest_df = self.manifest_df.ix[
            self.manifest_df.model_name.isin(models_names_to_write)
        ]

        for (_, row) in sub_manifest_df.iterrows():
            model_path = join(models_dir, "%s.pickle" % row.name)
            with open(join(model_path), 'wb') as fd:
                pickle.dump(row.model, fd, protocol=2)
            print("Wrote: %s" % model_path)

        write_manifest_df = self.manifest_df[[
            c for c in self.manifest_df.columns if c != "model"
        ]]
        manifest_path = join(models_dir, "manifest.csv")
        write_manifest_df.to_csv(manifest_path, index=False)
        print("Wrote: %s" % manifest_path)

    @staticmethod
    def model_name(allele, num):
        random_string = hashlib.sha1(
            str(time.time()).encode()).hexdigest()[:16]
        return "%s-%d-%s" % (allele, num, random_string)

    @staticmethod
    def load(models_dir, max_models=None):
        manifest_path = join(models_dir, "manifest.csv")
        manifest_df = pandas.read_csv(manifest_path, nrows=max_models)
        manifest_df["hyperparameters"] = manifest_df.hyperparameters.map(eval)
        manifest_df["history"] = manifest_df.history.map(eval)

        allele_to_allele_specific_models = collections.defaultdict(list)
        class1_pan_allele_models = []
        all_models = []
        for (_, row) in manifest_df.iterrows():
            model_path = join(models_dir, "%s.pickle" % row["name"])
            print("Loading model: %s" % model_path)
            with open(model_path, 'rb') as fd:
                model = pickle.load(fd)
            if row.allele == "pan-class1":
                class1_pan_allele_models.append(model)
            else:
                allele_to_allele_specific_models[row.allele].append(model)
            all_models.append(model)

        manifest_df["model"] = all_models

        pseudosequences = None
        if exists(join(models_dir, "pseudosequences.csv")):
            pseudosequences = pandas.read_csv(
                join(models_dir, "pseudosequences.csv"),
                index_col="allele").to_dict()

        print(
            "Loaded %d class1 pan allele predictors, %d pseudosequences, and "
            "%d allele specific models: %s" % (
                len(class1_pan_allele_models),
                len(pseudosequences) if pseudosequences else 0,
                sum(len(v) for v in allele_to_allele_specific_models.values()),
                ", ".join(
                    "%s (%d)" % (allele, len(v))
                    for (allele, v)
                    in sorted(allele_to_allele_specific_models.items()))))

        result = MultiAllelePredictorEnsemble(
            allele_to_allele_specific_models=allele_to_allele_specific_models,
            class1_pan_allele_models=class1_pan_allele_models,
            allele_to_pseudosequence=pseudosequences,
            manifest_df=manifest_df)
        return result

    def fit_allele_specific_predictors(
            self,
            n_models,
            architecture_hyperparameters,
            allele,
            peptides,
            affinities,
            output_assignments=None,
            models_dir_for_save=None,
            verbose=1):

        allele = mhcnames.normalize_allele_name(allele)
        models = self._fit_predictors(
            n_models=n_models,
            architecture_hyperparameters=architecture_hyperparameters,
            peptide=peptides,
            affinities=affinities,
            output_assignments=output_assignments,
            allele_pseudosequences=None,
            verbose=verbose)

        models_list = []
        for (i, model) in enumerate(models):
            name = self.model_name(allele, i)
            models_list.append(model)  # models is a generator
            row = pandas.Series({
                "allele": allele,
                "hyperparameters": architecture_hyperparameters,
                "history": model.fit_history.history,
                "name": name,
                "num_measurements": len(peptides),
                "fit_seconds": model.fit_seconds,
                "model": model,
            }).to_frame().T
            self.manifest_df = pandas.concat(
                [self.manifest_df, row], ignore_index=True)
            if models_dir_for_save:
                self.save(models_dir_for_save, model_names_to_write=[name])

        if allele not in self.allele_to_allele_specific_models:
            self.allele_to_allele_specific_models[allele] = []
        self.allele_to_allele_specific_models[allele].extend(models_list)
        return models

    def fit_class1_pan_allele_models(
            self,
            n_models,
            architecture_hyperparameters,
            alleles,
            peptides,
            affinities,
            output_assignments=None,
            models_dir_for_save=None,
            verbose=1):

        alleles = pandas.Series(alleles).map(mhcnames.normalize_allele_name)
        allele_pseudosequences = alleles.map(self.allele_to_pseudosequence)

        models = self._fit_predictors(
            n_models=n_models,
            architecture_hyperparameters=architecture_hyperparameters,
            peptides=peptides,
            affinities=affinities,
            output_assignments=output_assignments,
            allele_pseudosequences=allele_pseudosequences)

        models_list = []
        for (i, model) in enumerate(models):
            name = self.model_name("pan-class1", i)
            models_list.append(model)  # models is a generator
            row = pandas.Series({
                "allele": "pan-class1",
                "hyperparameters": architecture_hyperparameters,
                "history": model.fit_history.history,
                "name": name,
                "num_measurements": len(peptides),
                "fit_seconds": model.fit_seconds,
                "model": model,
            }).to_frame().T
            self.manifest_df = pandas.concat(
                [self.manifest_df, row], ignore_index=True)
            if models_dir_for_save:
                self.save(models_dir_for_save, model_names_to_write=[name])

        self.class1_pan_allele_models.extend(models_list)
        return models

    def _fit_predictors(
            self,
            n_models,
            architecture_hyperparameters,
            peptides,
            affinities,
            output_assignments,
            allele_pseudosequences,
            verbose=1):

        encodable_peptides = EncodableSequences.create(peptides)
        if output_assignments is None:
            output_assignments = ["output"] * len(encodable_peptides.sequences)
        for i in range(n_models):
            print("Training model %d / %d" % (i + 1, n_models))
            model = Class1BindingPredictor(**architecture_hyperparameters)
            model.fit(
                encodable_peptides,
                affinities,
                output_assignments=output_assignments,
                allele_pseudosequences=allele_pseudosequences,
                verbose=verbose)
            yield model

    def predict(
            self,
            peptides,
            alleles,
            include_mean=True,
            include_peptides_and_alleles=True):
        input_df = pandas.DataFrame({
            'peptide': peptides,
            'allele': alleles,
        })
        input_df["allele"] = input_df.allele.map(
            mhcnames.normalize_allele_name)

        result_dataframes = []

        if self.class1_pan_allele_models:
            allele_pseudosequences = input_df.allele.map(
                self.allele_to_pseudosequence)
            encodable_peptides = EncodableSequences.create(
                input_df.peptide.values)
            for model in self.class1_pan_allele_models:
                result_df = pandas.DataFrame(
                    model.predict(
                        encodable_peptides,
                        allele_pseudosequences=allele_pseudosequences))
                result_dataframes.append(result_df)

        if self.allele_to_allele_specific_models:
            for allele in input_df.allele.unique():
                mask = (input_df.allele == allele).values
                allele_peptides = EncodableSequences.create(
                    input_df.ix[mask].peptide.values)
                models = self.allele_to_allele_specific_models.get(allele, [])
                for model in models:
                    result_df = pandas.DataFrame(
                        model.predict(allele_peptides),
                        index=input_df.index[mask].values)
                    result_dataframes.append(result_df)

        model_predictions = pandas.Panel(
            dict(enumerate(result_dataframes)),
            major_axis=input_df.index)

        # Geometric mean
        log_means = numpy.log(model_predictions).mean(0)
        first_columns = []
        if include_mean:
            log_means["mean"] = log_means.mean(1)
            first_columns.append("mean")

        result = numpy.exp(log_means)

        if include_peptides_and_alleles:
            result["peptide"] = input_df.peptide.values
            result["allele"] = input_df.allele.values
            first_columns.append("allele")
            first_columns.append("peptide")

        assert len(result) == len(peptides), result.shape
        return result[
            list(reversed(first_columns)) +
            [c for c in result.columns if c not in first_columns]
        ]
