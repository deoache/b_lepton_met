import json
import copy
import pickle
import numpy as np
import awkward as ak
import importlib.resources
from coffea import processor
from coffea.analysis_tools import Weights, PackedSelection
from wprime_plus_b.corrections.jec import jet_corrections
from wprime_plus_b.corrections.met import met_phi_corrections
from wprime_plus_b.corrections.btag import BTagCorrector
from wprime_plus_b.corrections.pileup import add_pileup_weight
from wprime_plus_b.corrections.pujetid import add_pujetid_weight
from wprime_plus_b.corrections.l1prefiring import add_l1prefiring_weight
from wprime_plus_b.corrections.lepton import ElectronCorrector, MuonCorrector
from wprime_plus_b.processors.utils.analysis_utils import delta_r_mask, normalize
from wprime_plus_b.selections.ztoll.jet_selection import select_good_bjets
from wprime_plus_b.selections.ztoll.config import (
    ztoll_electron_selection,
    ztoll_muon_selection,
    ztoll_jet_selection,
)
from wprime_plus_b.selections.ztoll.lepton_selection import (
    select_good_electrons,
    select_good_muons,
)


class ZToLLProcessor(processor.ProcessorABC):
    def __init__(
        self,
        year: str = "2017",
        yearmod: str = "",
        lepton_flavor: str = "ele",
        output_type="array",
    ):
        self._year = year
        self._yearmod = yearmod
        self._lepton_flavor = lepton_flavor
        self._output_type = output_type

        # initialize output histogram
        """
        self.hist_dict = {
            "ptl1": utils.histograms.ptl1_histogram,
            "ptl2": utils.histograms.ptl2_histogram,
        }
        """
        # define dictionary to store analysis variables
        self.features = {}
        # initialize dictionary of arrays
        self.array_dict = {}

    def add_feature(self, name: str, var: ak.Array) -> None:
        """add a variable array to the out dictionary"""
        self.features = {**self.features, name: var}

    def process(self, events):
        # dictionary to store output data and metadata
        output = {}

        # get dataset name
        dataset = events.metadata["dataset"]

        # check if sample is MC
        self.is_mc = hasattr(events, "genWeight")

        # get number of events before selection
        nevents = len(events)

        # create copies of histogram objects
        # hist_dict = copy.deepcopy(self.hist_dict)
        # create copy of array dictionary
        array_dict = copy.deepcopy(self.array_dict)

        # ------------------
        # event preselection
        # ------------------
        # select good electrons
        if self._lepton_flavor == "ele":
            good_electrons = select_good_electrons(
                events=events,
                electron_pt_threshold=ztoll_electron_selection["electron_pt_threshold"],
                electron_id_wp=ztoll_electron_selection["electron_id_wp"],
                electron_iso_wp=ztoll_electron_selection["electron_iso_wp"],
            )
            electrons = events.Electron[good_electrons]
        # select good muons
        if self._lepton_flavor == "mu":
            good_muons = select_good_muons(
                events=events,
                muon_pt_threshold=ztoll_muon_selection["muon_pt_threshold"],
                muon_id_wp=ztoll_muon_selection["muon_id_wp"],
                muon_iso_wp=ztoll_muon_selection["muon_iso_wp"],
            )
            muons = events.Muon[good_muons]
        # define leptons collection
        good_leptons = electrons if self._lepton_flavor == "ele" else muons

        # apply JEC/JER corrections to MC jets (propagate corrections to MET)
        # in data, the corrections are already applied
        if self.is_mc:
            jets, met = jet_corrections(events, self._year + self._yearmod)
        else:
            jets, met = events.Jet, events.MET
            
        # select good bjets
        good_bjets = select_good_bjets(
            jets=jets,
            year=self._year + self._yearmod,
            jet_pt_threshold=ztoll_jet_selection["jet_pt_threshold"],
            jet_id=ztoll_jet_selection["jet_id"],
            jet_pileup_id=ztoll_jet_selection["jet_pileup_id"],
            btag_working_point=ztoll_jet_selection["btag_working_point"],
        ) & (delta_r_mask(jets, good_leptons, threshold=0.4))
        bjets = jets[good_bjets]

        # apply MET phi corrections
        met_pt, met_phi = met_phi_corrections(
            met_pt=met.pt,
            met_phi=met.phi,
            npvs=events.PV.npvsGood,
            is_mc=self.is_mc,
            year=self._year,
            year_mod=self._yearmod,
        )
        met["pt"], met["phi"] = met_pt, met_phi

        # --------------------
        # event weights vector
        # --------------------
        weights_container = Weights(len(events), storeIndividual=True)
        if self.is_mc:
            # add gen weigths
            weights_container.add("genweight", events.genWeight)

            # add l1prefiring weigths
            add_l1prefiring_weight(events, weights_container, self._year, "nominal")

            # add pileup weigths
            add_pileup_weight(
                events, weights_container, self._year, self._yearmod, "nominal"
            )

            # add pujetid weigths
            add_pujetid_weight(
                jets=jets,
                weights=weights_container,
                year=self._year,
                year_mod=self._yearmod,
                working_point=ztoll_jet_selection["btag_working_point"],
                variation="nominal",
            )

            # b-tagging corrector
            btag_corrector = BTagCorrector(
                jets=jets,
                weights=weights_container,
                sf_type="comb",
                worging_point=ztoll_jet_selection["btag_working_point"],
                tagger="deepJet",
                year=self._year,
                year_mod=self._yearmod,
                full_run=False,
                variation="nominal",
            )
            # add b-tagging weights
            btag_corrector.add_btag_weights(flavor="bc")

            if self._lepton_flavor == "ele":
                electron_corrector = ElectronCorrector(
                    electrons=events.Electron,
                    weights=weights_container,
                    year=self._year,
                    year_mod=self._yearmod,
                    variation="nominal",
                )
                # add electron ID weights
                electron_corrector.add_id_weight(
                    id_working_point=ztoll_electron_selection["electron_id_wp"]
                )
                # add electron reco weights
                electron_corrector.add_reco_weight()
            else:
                # muon corrector
                muon_corrector = MuonCorrector(
                    muons=events.Muon,
                    weights=weights_container,
                    year=self._year,
                    year_mod=self._yearmod,
                    variation="nominal",
                    id_wp=ztoll_muon_selection["muon_id_wp"],
                    iso_wp=ztoll_muon_selection["muon_iso_wp"],
                )
                # add muon ID weights
                muon_corrector.add_id_weight()
                # add muon iso weights
                muon_corrector.add_iso_weight()
                # add muons triggerIso weights
                muon_corrector.add_triggeriso_weight()
                
        # get sum of weights before selections
        output["metadata"] = {"sumw": ak.sum(weights_container.weight())}
        # save weight statistics
        output["metadata"].update({"weight_statistics": {}})
        for weight, statistics in weights_container.weightStatistics.items():
            output["metadata"]["weight_statistics"][weight] = statistics
        # ------------
        # variables
        # ------------
        # leading leptons
        leading_lepton = ak.firsts(good_leptons)
        subleading_lepton = ak.pad_none(good_leptons, 2)[:, 1]
        ptl1 = leading_lepton.pt
        ptl2 = subleading_lepton.pt

        # transverse momentum of the sum of the two leading lepton 4-momenta
        leading_leptons = leading_lepton + subleading_lepton
        ptll = leading_leptons.pt

        # invariant mass of the sum of the two leading lepton 4-momenta
        mll = leading_leptons.mass

        # Δφ between the two leading leptons
        dphill = leading_lepton.delta_phi(subleading_lepton)

        # ΔR between the two leading leptons
        drll = leading_lepton.delta_r(subleading_lepton)

        # transverse mass of the candidate made by the two leading leptons and the MET
        mth = np.sqrt(
            2.0
            * leading_leptons.pt
            * met.pt
            * (ak.ones_like(met.pt) - np.cos(leading_leptons.delta_phi(met)))
        )
        # number of primary vertex
        nvtx = events.PV.npvsGood

        # ---------------
        # event selection
        # ---------------
        # make a PackedSelection object to manage selections
        self.selections = PackedSelection()

        # add luminosity calibration mask (only to data)
        with importlib.resources.path("wprime_plus_b.data", "lumi_masks.pkl") as path:
            with open(path, "rb") as handle:
                self._lumi_mask = pickle.load(handle)
        if not self.is_mc:
            lumi_mask = self._lumi_mask[self._year](events.run, events.luminosityBlock)
        else:
            lumi_mask = np.ones(len(events), dtype="bool")
        self.selections.add("lumi", lumi_mask)

        # add lepton triggers masks
        with importlib.resources.path("wprime_plus_b.data", "triggers.json") as path:
            with open(path, "r") as handle:
                self._triggers = json.load(handle)[self._year]
        trigger = {}
        for ch in ["ele", "mu"]:
            trigger[ch] = np.zeros(nevents, dtype="bool")
            for t in self._triggers[ch]:
                if t in events.HLT.fields:
                    trigger[ch] = trigger[ch] | events.HLT[t]
        self.selections.add("trigger_ele", trigger["ele"])
        self.selections.add("trigger_mu", trigger["mu"])

        # add MET filters mask
        with importlib.resources.path("wprime_plus_b.data", "metfilters.json") as path:
            with open(path, "r") as handle:
                self._metfilters = json.load(handle)[self._year]
        metfilters = np.ones(nevents, dtype="bool")
        metfilterkey = "mc" if self.is_mc else "data"
        for mf in self._metfilters[metfilterkey]:
            if mf in events.Flag.fields:
                metfilters = metfilters & events.Flag[mf]
        self.selections.add("metfilters", metfilters)

        # good vertices
        self.selections.add("goodvertex", events.PV.npvsGood > 0)
        # check that we have 2l events
        self.selections.add("two_leptons", ak.num(good_leptons) == 2)
        # check that dilepton system is neutral
        self.selections.add(
            "neutral", leading_lepton.charge * subleading_lepton.charge < 0
        )
        # check that dilepton invariant mass is between 60 and 120 GeV
        self.selections.add("mass_range", (mll > 60) & (mll < 120))
        # veto bjets
        self.selections.add("bjet_veto", ak.num(bjets) == 0)
        # transverse mass
        self.selections.add("mthlt60", mth < 60)

        self.selections.add("ee", ak.prod(good_leptons.pdgId, axis=1) == -11 * 11)
        self.selections.add("mumu", ak.prod(good_leptons.pdgId, axis=1) == -13 * 13)
        self.selections.add("emu", ak.prod(good_leptons.pdgId, axis=1) == -11 * 13)

        # define selection regions for each lepton_channel
        regions = {
            "ele": [
                "goodvertex",
                "lumi",
                "trigger_ele",
                "metfilters",
                "bjet_veto",
                "two_leptons",
                "neutral",
                "mass_range",
                "mthlt60",
                "ee",
            ],
            "mu": [
                "goodvertex",
                "lumi",
                "trigger_mu",
                "metfilters",
                "bjet_veto",
                "two_leptons",
                "neutral",
                "mass_range",
                "mthlt60",
                "mumu",
            ],
        }

        # --------------
        # cutflow
        # --------------
        cut_names = regions[self._lepton_flavor]
        output["metadata"].update({"cutflow": {}})
        selections = []
        for cut_name in cut_names:
            selections.append(cut_name)
            current_selection = self.selections.all(*selections)
            output["metadata"]["cutflow"][cut_name] = ak.sum(
                weights_container.weight()[current_selection]
            )
        # ---------------
        # event variables
        # ---------------
        for lepton_flavor in regions:
            if lepton_flavor != self._lepton_flavor:
                continue

            region_selection = self.selections.all(*regions[lepton_flavor])
            # if there are no events left after selection cuts continue to the next .root file
            nevents_after = ak.sum(region_selection)
            if nevents_after == 0:
                continue
            # select region objects
            self.add_feature("ptl1", ptl1[region_selection])
            self.add_feature("ptl2", ptl2[region_selection])
            self.add_feature("ptll", ptll[region_selection])
            self.add_feature("mll", mll[region_selection])
            self.add_feature("dphill", dphill[region_selection])
            self.add_feature("drll", drll[region_selection])
            self.add_feature("mth", mth[region_selection])
            self.add_feature("nvtx", nvtx[region_selection])

            # -----------------------------
            # fill histogram
            # -----------------------------
            """
            if self._output_type == "hist":
                for kin in hist_dict:
                    fill_args = {
                        feature: utils.analysis_utils.normalize(self.features[feature])
                        for feature in hist_dict[kin].axes.name
                        if "dataset" not in feature
                    }
                    hist_dict[kin].fill(
                        **fill_args,
                        dataset=dataset,
                        weight=region_weights,
                    )
            else:
            """
            region_weights = weights_container.weight()[region_selection]
            self.add_feature("weights", region_weights)

            # select variables and put them in column accumulators
            array_dict = {
                feature_name: processor.column_accumulator(normalize(feature_array))
                for feature_name, feature_array in self.features.items()
            }
        # define output dictionary accumulator
        output["arrays"] = array_dict
        # save metadata
        output["metadata"].update(
            {
                "events_before": nevents,
                "events_after": nevents_after,
            }
        )
        return output

    def postprocess(self, accumulator):
        return accumulator