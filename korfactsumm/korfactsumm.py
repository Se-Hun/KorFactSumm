from typing import Dict, List, Set, Tuple, Union

import kss

from korfactsumm.utils.module_entity import load_ie, load_ner, load_rel
from korfactsumm.utils.module_question import load_qa, load_qg
from korfactsumm.utils.module_rouge import Rouge
from korfactsumm.utils.module_sentence import load_bert_score
from korfactsumm.utils.utils import Config, qags_score

class KorFactSumm:

    def __init__(
        self,
        ner_model: str = None,
        rel_model: str = None,
        qa_model: str = None,
        qg_model: str = None,
        bert_score_model: str = None
    ):
        """
        Args:
            ner_model (str, optional):
            rel_model (str, optional):
            qa_model (str, optional):
            qg_model (str, optional):
            bert_score_model (str, optional):
        """
        self.config = Config()
        self.segmenter = kss.split_sentences # for korean sentence segmenter
        self.rouge = Rouge(max_n=2, metrics=["rouge-n", "rouge-l"])

        # NER, RE, QG, QA models supported by HuggingFace can be used (default can be found in `config.py`)
        self.ner = ner_model if ner_model is not None else self.config.NER_MODEL
        self.rel = rel_model if rel_model is not None else self.config.REL_MODEL
        self.qg = qg_model if qg_model is not None else self.config.QG_MODEL
        self.qa = qa_model if qa_model is not None else self.config.QA_MODEL
        self.bert_score = bert_score_model if bert_score_model is not None else self.config.BERT_SCORE_MODEL
        self.ie = None

    def _segment(self, text: str) -> List[str]:
        """
        Segment input text into (possibly) multiple sentences
        Args:
            text (str): text to be segmented
        Returns:
            List[str]: list of segmented lines
        """
        return [line.strip() for line in self.segmenter(text)]

    def _print_entities(self, mode: str, total_entities: List[List[Dict]]):
        print(f"{mode.upper()} Entities")
        for i, line_entities in enumerate(total_entities):
            print(f'{i+1}: {[(entity["word"], entity["entity"]) for entity in line_entities]}')
        print()

    def calculate_rouge(
        self,
        source: str,
        summary: str,
    ) -> Tuple[float, float, float]:
        """
        Calculate ROUGE score
        Args:
            source (str): original source
            summary (str): generated summary
        Returns:
            Tuple: (ROUGE-1, ROUGE-2, ROUGE-L) tuple
        """
        source_lines = self._segment(source)

        rouges = self.rouge.get_scores(summary, source_lines)

        rouge_1 = rouges["rouge-1"]["f"]
        rouge_2 = rouges["rouge-2"]["f"]
        rouge_l = rouges["rouge-l"]["f"]

        print(
            f"Avg. ROUGE-1: {rouge_1}\nAvg. ROUGE-2: {rouge_2}\nAvg. ROUGE-L: {rouge_l}"
        )
        return rouge_1, rouge_2, rouge_l

    def _print_facts(self, mode: str, facts: Set[Tuple]):
        print(f"{mode.upper()} Facts")
        for fact in facts:
            print(fact)
        print()

    def _filter_out(self, sources: Set, summaries: Set) -> Tuple[Set, Set]:
        """
        Filter out triples that don't share a subject and relation for comparability
        Args:
            sources (Set): set of triples from source
            summaries (Set): set of triples from summary
        Returns:
            Tuple[Set, Set]: filtered sources and summaries
        """
        source_tuple = {(source[0], source[1]) for source in sources}
        summary_tuple = {(summary[0], summary[1]) for summary in summaries}

        sources = {
            source for source in sources
            if (source[0], source[1]) in summary_tuple
        }
        summaries = {
            summary for summary in summaries
            if (summary[0], summary[1]) in source_tuple
        }
        return sources, summaries

    def extract_facts(
        self,
        source: str,
        summary: str,
        verbose: bool = False,
        device: str = "cpu",
    ):
        """
        Extract (head_entity, relation, tail_entity) relation triple using NER & RE module
            See also https://arxiv.org/abs/1905.13322.pdf
        Args:
            source (str): original source
            summary (str): generated summary
            verbose (bool, optional): print verbose option. Defaults to False.
            device (str): device info
        """
        if isinstance(self.ner, str) and isinstance(self.rel, str):
            self.ner = load_ner(self.ner, device)
            self.rel = load_rel(self.rel, device)

        source_lines = self._segment(source)
        summary_lines = self._segment(summary)

        # extract per-line entities
        source_ents = self.ner(source_lines)
        summary_ents = self.ner(summary_lines)

        # extract entity-based triple: (head, relation, tail)
        source_facts = self.get_facts(source_lines, source_ents)
        summary_facts = self.get_facts(summary_lines, summary_ents)

        # filter out some facts
        source_facts, summary_facts = self._filter_out(
            source_facts,
            summary_facts,
        )

        common_facts = summary_facts.intersection(source_facts)
        diff_facts = summary_facts.difference(source_facts)

        if verbose:
            self._print_entities("source", source_ents)
            self._print_entities("summary", summary_ents)

            self._print_facts("source", source_facts)
            self._print_facts("summary", summary_facts)

            self._print_facts("common", common_facts)
            self._print_facts("diff", diff_facts)

        if not summary_facts:
            fact_score = 0.0
        else:
            fact_score = len(common_facts) / len(summary_facts)
        print(f"Fact Score: {fact_score}")

        return source_ents, summary_ents, fact_score

    def _print_qas(self, mode: str, questions: List[Dict]):
        print(f"Answers based on {mode.upper()} (Questions are generated from Summary)")
        for question in questions:
            print(f"[Q] {question['question']}\t[Pred] {question['prediction']}")
        print()

    def extract_qas(
        self,
        source: str,
        summary: str,
        source_ents: List = None,
        summary_ents: List = None,
        verbose: bool = False,
        device: str = "cpu",
    ) -> float:
        """
        Extract Question & Answering Pair generated from Question Generation module
            See also https://arxiv.org/abs/2004.04228
        Args:
            source (str): original source
            summary (str): generated summary
            source_ents (List, optional): named entities extracted from source. Defaults to None.
            summary_ents (List, optional): named entities extracted from source. Defaults to None.
            verbose (bool, optional): print verbose option. Defaults to False.
            device (str): device info
        """
        if isinstance(self.qg, str) and isinstance(self.qa, str):
            self.qg = load_qg(self.qg, device)
            self.qa = load_qa(self.qa, device)

        if isinstance(self.ner, str):
            self.ner = load_ner(self.ner, device)

        source_lines = self._segment(source) # TODO : Not Use -- 지울 것
        summary_lines = self._segment(summary)

        if source_ents is None: # TODO : Not Use -- 지울 것
            source_ents = self.ner(source_lines) # TODO : Not Use -- 지울 것

        if summary_ents is None:
            summary_ents = self.ner(summary_lines)

        summary_qas = self.qg(summary_lines, summary_ents)

        source_answers = self.qa(source, summary_qas)
        summary_answers = self.qa(summary, summary_qas)

        if verbose:
            self._print_qas("source", source_answers)
            self._print_qas("summary", summary_answers)

        qa_score = qags_score(source_answers, summary_answers)
        print(f"QAGS Score: {qa_score}\n")

        return qa_score

    def _print_triples(self, mode: str, triples: Set):
        print(f"{mode.upper()} Triples")
        for triple in triples:
            print(triple)
        print()

    def extract_triples(
        self,
        source: str,
        summary: str,
        verbose: bool = False
    ):
        """
        Extract OpenIE based fact triples
        Args:
            source (str): original source
            summary (str): generated summary
            verbose (bool, optional): print verbose option. Defaults to False.
        """
        if self.ie is None:
            self.ie = load_ie()

        source_triples = {(
            triple["subject"],
            triple["relation"],
            triple["object"],
        ) for triple in self.ie(source)}

        summary_triples = {(
            triple["subject"],
            triple["relation"],
            triple["object"],
        ) for triple in self.ie(summary)}

        source_triples, summary_triples = self._filter_out(
            source_triples,
            summary_triples,
        )

        if verbose:
            self._print_triples("source", source_triples)
            self._print_triples("summary", summary_triples)

        common_triples = summary_triples.intersection(source_triples)

        if not summary_triples:
            triple_score = 0.0
        else:
            triple_score = len(common_triples) / len(summary_triples)

        print(f"Triple Score: {triple_score}\n")

        return triple_score

    def calculate_bert_score(
        self,
        source: str,
        summary: str,
        device: str = "cpu",
    ) -> List[float]:
        """
        Calculate BERTScore
            See also https://arxiv.org/abs/2005.03754
        Args:
            source (str): original source
            summary (str): generated summary
            device (str): device info
        Returns:
            List: (Precision, Recall, F1) BERTScore list
        """
        if isinstance(self.bert_score, str):
            self.bert_score = load_bert_score(self.bert_score, device)

        # TODO --> BUG: When len(source_lines) == 1, bmm error raises
        source_lines = self._segment(source)
        summary_lines = [summary, "dummy"]

        scores = self.bert_score(summary_lines, source_lines)
        filtered_scores = list()

        for score in scores:
            score = score.tolist()
            score.pop(-1)
            filtered_scores.append(sum(score) / len(score))

        print(
            f"BERTScore Score\nPrecision: {filtered_scores[0]}\nRecall: {filtered_scores[1]}\nF1: {filtered_scores[2]}"
        )

        return filtered_scores

    def __call__(
        self,
        sources: Union[List[str], str],
        summaries: Union[List[str], str],
        verbose: bool = False,
        device: str = "cpu"
    ) -> Dict:
        """

        Args:
            sources:
            summaries:
            verbose:
            device:

        Returns:

        """
        if isinstance(sources, str) and isinstance(summaries, str):
            sources = [sources]
            summaries = [summaries]

        if len(sources) != len(summaries):
            raise ValueError("`sources` and `summaries` must have the same number of elements!")

        num_pairs = len(sources)

        fact_scores = 0
        qags_scores = 0
        triple_scores = 0
        rouges = [0, 0, 0]
        bert_scores = [0, 0, 0]

        for source, summary in zip(sources, summaries):
            # TODO : 한국어 Relation Extraction 모델 찾거나 만들어서 Huggingface models에 배포
            # source_ents, summary_ents, fact_score = self.extract_facts(
            #     source,
            #     summary,
            #     verbose,
            #     device
            # )
            # fact_scores += fact_score

            # TODO : QG 모델 생기면 그때 할 것
            # qags_score = self.extract_qas(
            #     source,
            #     summary,
            #     source_ents,
            #     summary_ents,
            #     verbose,
            #     device,
            # )
            # qags_scores += qags_score
            
            # TODO : Korean OpenIE 필요
            # triple_score = self.extract_triples(source, summary, verbose)
            # triple_scores += triple_score

            rouge_1, rouge_2, rouge_l = self.calculate_rouge(source, summary)
            rouges[0] += rouge_1
            rouges[1] += rouge_2
            rouges[2] += rouge_l

            # TODO : bmm error 발생
            bert_score = self.calculate_bert_score(source, summary, device)
            bert_scores[0] += bert_score[0]
            bert_scores[1] += bert_score[1]
            bert_scores[2] += bert_score[2]

        return {
            "fact_score": fact_scores / num_pairs,
            "qa_score": qags_scores / num_pairs,
            "triple_score": triple_scores / num_pairs,
            "rouge": (
                rouges[0] / num_pairs,
                rouges[1] / num_pairs,
                rouges[2] / num_pairs,
            ),
            "bert_score": {
                "precision": bert_scores[0],
                "recall": bert_scores[1],
                "f1": bert_scores[2],
            },
        }