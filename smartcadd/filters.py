from typing import List, Dict
import pandas as pd
from multiprocessing import Pool

from .model_wrappers import ModelWrapper
from .data import Compound, SMARTS_Query


class Filter:
    def __init__(self, filter_config: Dict = None, output_dir: str = None):
        self.filter_config = filter_config
        self.output_dir = output_dir

    def __call__(self, batch: List[Compound]) -> List[Compound]:
        return self.run(batch)

    def run(self, batch: List[Compound]) -> List[Compound]:
        raise NotImplementedError(
            "This method should be implemented in the subclass."
        )

    def save(self, batch: List[Compound], output_file: str = None) -> None:
        raise NotImplementedError(
            "This method should be implemented in the subclass."
        )

    def _filter(self, compound: Compound) -> Compound:
        raise NotImplementedError(
            "This method should be implemented in the subclass."
        )


class DummyFilter(Filter):
    """
    Dummy filter for testing purposes

    Args:
        filter_config (dict): configuration for filter
    """

    def __init__(self, filter_config: Dict = None):
        super().__init__(filter_config)

    def run(self, batch: List[Compound]) -> List[Compound]:
        """
        Dummy filter that returns the input batch

        Args:
            batch: list of Compound objects

        Returns:
            batch: list of Compound objects
        """

        return batch

    def save(
        self, batch: List[Compound], output_file: str = "dummy_filtered.csv"
    ):
        """
        Save results to csv with SMILES, ID, **descriptors

        Args:
            batch (List[Compound]): list of Compound objects
            output_file (str): output file path. Default is "dummy_filtered.csv"
        """

        df = pd.DataFrame.from_records(
            [compound.to_dict() for compound in batch]
        )

        df.to_csv(output_file, index=False)


class ADMETFilter(Filter):
    """
    Filter compounds based on ADMET PAINS patterns

    Args:
        filter_config (dict): configuration for filter
        n_processes (int): number of processes to use for filtering

    Config:
        save (bool): save filtered compounds to csv. Default is False

    Returns:
        filtered_batch: list of filtered Compound objects based on ADMET PAINS patterns

    """

    def __init__(self, filter_config, n_processes=1):
        super().__init__(filter_config)
        self.n_processes = n_processes

        assert (
            "alert_collection_path" in filter_config.keys()
        ), "alert_collection_path not found in filter_config"

        if "save" in filter_config.keys():
            self.save = filter_config["save"]
        else:
            self.save = False

        self._pains_patterns = self._init(
            alert_collection_path=filter_config["alert_collection_path"]
        )

    def run(self, batch: List[Compound]) -> List[Compound]:
        """
        Filters compounds based on ADMET PAINS patterns

        Args:
            batch: list of Compound objects

        Returns:
            filtered_batch: list of filtered Compound objects
        """

        with Pool(self.n_processes) as pool:
            mask = pool.map(self._filter, batch)

        # remove None values
        filtered_batch = [
            compound for compound, keep in zip(batch, mask) if keep
        ]

        if self.save:
            self.save(zip(batch, mask))

        return filtered_batch

    def save(
        self, batch: List[Compound], output_file: str = "ADMET_filtered.csv"
    ) -> None:
        """
        Save results to csv with SMILES, ID, **descriptors

        Args:
            batch (List[Compound]): list of Compound objects
            output_file (str): output file path. Default is "ADMET_filtered.csv"
        """

        df = pd.DataFrame.from_records(
            [
                compound.to_dict() | {"keep": keep}
                for compound, keep in batch
                if keep
            ]
        )

        df.to_csv(output_file, index=False)

    def _init(self, alert_collection_path: str) -> List[SMARTS_Query]:
        """
        Initialize ADMET PAINS patterns for filtering
        """

        try:
            rule_df = pd.read_csv(alert_collection_path)
        except Exception as e:
            print(f"Error reading alert collection file: {e}")
            raise e

        PAINS_df = rule_df[rule_df["rule_set_name"] == "PAINS"]

        temp_list = [
            SMARTS_Query(smarts, max_val, desc)
            for smarts, max_val, desc in PAINS_df[
                ["smarts", "max", "description"]
            ].values
        ]

        return [query for query in temp_list if query.pattern]

    def _filter(self, compound: Compound) -> Compound:
        """
        Filter a single compound based on ADMET PAINS patterns
        """

        if compound.descriptors["status"] == "INVALID":
            return None

        for smarts_query in self._pains_patterns:
            if (
                len(compound.mol.GetSubstructMatches(smarts_query.pattern))
                > smarts_query.max_val
            ):
                compound.descriptors["status"] = (
                    f"{smarts_query.desc} > {smarts_query.max_val}"
                )
                return False

        return True


class ModelFilter(Filter):
    """
    Filter compounds based on model prediction probabilities

    Args:
        filter_config (dict): configuration for filter
        threshold (float): threshold for filtering

    Config:
        save (bool): save filtered compounds to csv. Default is False

    Returns:
        filtered_batch: list of filtered Compound objects based on model
            prediction probabilities > threshold
    """

    def __init__(
        self,
        model_wrapper: ModelWrapper,
        filter_config: Dict = None,
        target: int = 0,
        threshold: float = 0.5,
    ):
        super().__init__(filter_config)

        self.model_wrapper = model_wrapper
        self.target = target
        self.threshold = threshold

        if "save" in filter_config.keys():
            self.save = filter_config["save"]
        else:
            self.save = False

        # load model weights
        try:
            self.model_wrapper.load()
        except Exception as e:
            print(f"Error loading model weights: {e}")
            raise e

    def predict(self, batch: List[Compound], target: int = 0) -> List[float]:
        """
        Predict on input batch using the model.

        Args:
            batch (list): list of Compound objects

        Config:
            save (bool): save filtered compounds to csv. Default is False

        Returns:
            list: list of prediction probabilities

        """

        featurized_batch = self.model.featurize(batch)

        return self.model_wrapper.predict(featurized_batch, self.target)

    def run(self, batch: List[Compound]) -> List[Compound]:
        """
        Filter compounds based on model prediction probabilities

        Args:
            batch (list): list of Compound objects

        Returns:
            list: list of filtered Compound objects

        """

        predictions = self.predict(batch, self.target)

        # filter based on threshold
        filtered_batch = [
            compound
            for idx, compound in enumerate(batch)
            if predictions[idx] > self.threshold
        ]

        if self.save:
            self.save(zip(batch, predictions))

        return filtered_batch

    def save(
        self, batch: List[Compound], output_file: str = "model_filtered.csv"
    ) -> None:
        """
        Save results to csv with SMILES, ID, prediction

        Args:
            batch (List[Compound]): list of Compound objects
            output_file (str): output file path. Default is "model_filtered.csv"
        """

        with open(output_file, "w") as f:
            f.write("SMILES,ID,Prediction\n")
            for compound, prediction in batch:
                f.write(
                    f"{compound.smiles},{compound.id},{round(float(prediction), 3)}\n"
                )


class PharmacophoreFilter2D(Filter):
    """
    Filter compounds based on 2D pharmacophore features of a set of template compounds

    Args:
        filter_config (dict): configuration for filter
        pharmacophore_df (pd.DataFrame): dataframe containing 2D pharmacophore features

    Config:
        save (bool): save filtered compounds to csv. Default is False

    Returns:
        filtered_batch: list of filtered Compound objects based on 2D pharmacophore features
    """

    def __init__(
        self,
        template_compounds: List[Compound],
        filter_config: Dict = None,
        n_processes: int = 1,
    ):
        super().__init__(filter_config)

        if "save" in filter_config.keys():
            self.save = filter_config["save"]
        else:
            self.save = False

        self.template_dict = self._preprocess_templates(template_compounds)
        self.n_processes = n_processes

    def run(self, batch: List[Compound]) -> List[Compound]:
        """
        Filter compounds based on 2D pharmacophore features of template compounds

        Args:
            batch (list): list of Compound objects

        Returns:
            list: list of filtered Compound objects
        """

        with Pool(self.n_processes) as pool:
            mask = pool.map(self._filter, batch)

        if self.save:
            self.save(zip(batch, mask))

        filtered_batch = [
            compound for compound, keep in zip(batch, mask) if keep
        ]

        return filtered_batch

    def save(
        self,
        batch: List[Compound],
        output_file: str = "2D_pharmacophore_filtered.csv",
    ) -> None:
        """
        Save results to csv with SMILES, ID, keep

        Args:
            batch (List[Compound]): list of Compound objects
            output_file (str): output file path. Default is "pharmacophore_filtered.csv"
        """

        df = pd.DataFrame.from_records(
            [
                compound.to_dict() | {"keep": keep}
                for compound, keep in batch
                if keep
            ]
        )

        df.to_csv(output_file, index=False)

    def _filter(self, compound: Compound) -> Compound:
        """
        Filter a single compound based on 2D pharmacophore features of template compounds

        Args:
            compound (Compound): compound object to filter

        Returns:
            Compound: filtered compound object
        """

        if compound.descriptors["status"] == "INVALID":
            return None

        flattened_compound = compound.to_dict()
        keep = (
            (
                flattened_compound["total_N_aro_members"]
                == self.template_dict["total_N_aro_members"]
            )
            and (
                flattened_compound["total_N_ali_members"]
                == self.template_dict["total_N_ali_members"]
            )
            and (
                flattened_compound["total_aro_N_count"]
                >= self.template_dict["total_aro_N_count"]
            )
            and (
                flattened_compound["total_aro_O_count"]
                >= self.template_dict["total_aro_O_count"]
            )
            and (
                flattened_compound["total_ali_O_count"]
                >= self.template_dict["total_ali_O_count"]
            )
            and (
                flattened_compound["total_ali_N_count"]
                >= self.template_dict["total_ali_N_count"]
            )(
                flattened_compound["NumHDonors"]
                >= self.template_dict["NumHDonors"]
            )
            and (
                flattened_compound["NumHAcceptors"]
                >= self.template_dict["NumHAcceptors"]
            )
        )

        if keep:
            return True

        return False

    def _preprocess_templates(
        self, template_compounds: List[Compound]
    ) -> Dict:
        """
        Preprocess template compounds to extract 2D pharmacophore features

        Args:
            template_compounds (list): list of template Compound objects

        Returns:
            pd.DataFrame: dataframe containing 2D pharmacophore features
        """

        template_df = pd.concat(
            [compound.to_df() for compound in template_compounds]
        )

        out_dict = template_df.min(axis=0).iloc[0].to_dict()

        return out_dict
