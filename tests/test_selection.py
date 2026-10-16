"""Unit tests for the chunk pre-selector (:mod:`scd.selection`).

These guard the "make big annual reports practical without losing evidence"
contract: KPI-bearing sections must be kept, pure boilerplate must be skipped,
every skip must carry a machine-readable reason, and the filter must never
renumber or drop pages from the index (it only chooses which chunks go to the
LLM).
"""

from __future__ import annotations

from scd import selection


def _chunk(text: str, *, doc_type: str = "annual_report", page: int = 1,
           source_doc: str = "annual_report.pdf") -> dict:
    return {
        "company": "Britannia Industries Limited",
        "doc_type": doc_type,
        "period": "FY24",
        "source_doc": source_doc,
        "page": page,
        "text": text,
        "chunk_id": f"{source_doc}::p{page}::c1",
        "meta": {},
    }


# ---------------------------------------------------------------------------
# Strong sections are always kept
# ---------------------------------------------------------------------------
class TestStrongSectionsKept:
    def test_management_discussion(self):
        chunk = _chunk(
            "Management Discussion and Analysis. The packaged foods industry "
            "grew 6% in FY24. Our revenue from operations grew 6.5% to "
            "Rs 14,500 crore driven by premiumisation."
        )
        keep, reason = selection.evaluate_chunk(chunk)
        assert keep
        assert reason == "section:management_discussion"

    def test_segment_information(self):
        chunk = _chunk(
            "Segment Information (Note 27). Segment revenue: Bakery & Dairy "
            "Rs 12,100 crore; Others Rs 2,400 crore. Segment result Rs 1,900 crore."
        )
        keep, reason = selection.evaluate_chunk(chunk)
        assert keep
        assert reason == "section:segment_information"

    def test_financial_highlights(self):
        chunk = _chunk(
            "Financial Highlights. Revenue Rs 14,512 crore (+6.5%). EBITDA "
            "Rs 2,260 crore. PAT Rs 1,140 crore, up 8% year-on-year."
        )
        keep, reason = selection.evaluate_chunk(chunk)
        assert keep
        assert reason == "section:financial_highlights"

    def test_shareholder_letter(self):
        chunk = _chunk(
            "Letter to the Shareholders. Dear Shareholder, the year gone by "
            "tested input costs. Gross margin recovered to 43.8% by Q4 FY24 "
            "and volumes returned to positive territory at 2% growth."
        )
        keep, reason = selection.evaluate_chunk(chunk)
        assert keep
        assert reason == "section:shareholder_letter"

    def test_trade_receivables_note(self):
        chunk = _chunk(
            "Note 12: Trade receivables. Ageing: over 12 months Rs 61 crore; "
            "within 6 months Rs 482 crore. Allowance for doubtful debts Rs 18 crore."
        )
        keep, reason = selection.evaluate_chunk(chunk)
        assert keep
        assert reason == "section:trade_receivables"

    def test_revenue_note(self):
        chunk = _chunk(
            "Disaggregation of revenue (Note 6). Revenue from operations: "
            "domestic Rs 13,900 crore; exports Rs 612 crore."
        )
        keep, reason = selection.evaluate_chunk(chunk)
        assert keep
        assert reason == "section:revenue_note"


# ---------------------------------------------------------------------------
# Pure boilerplate is skipped, with reasons
# ---------------------------------------------------------------------------
class TestBoilerplateSkipped:
    def test_table_of_contents(self):
        chunk = _chunk(
            "Table of Contents\nChairman's Statement 04\nFinancial Highlights "
            "06\nManagement Discussion and Analysis 12\nFinancial Results 48"
        )
        keep, reason = selection.evaluate_chunk(chunk)
        assert not keep
        assert reason == "boilerplate:table_of_contents"

    def test_cover_page(self):
        chunk = _chunk("Annual Report 2023-24", page=1)
        keep, reason = selection.evaluate_chunk(chunk)
        assert not keep
        assert reason == "boilerplate:cover"

    def test_registered_office_page(self):
        chunk = _chunk(
            "Corporate Information. Registered Office: 5/1A, Block A, "
            "Panchsheel Park, New Delhi - 110017. Corporate Identity Number "
            "L15490DL1918PLC007250."
        )
        keep, reason = selection.evaluate_chunk(chunk)
        assert not keep
        assert reason == "boilerplate:registered_office"

    def test_disclaimer_page(self):
        chunk = _chunk(
            "Disclaimer. This report contains forward-looking statements "
            "that involve risks and uncertainties. This document does not "
            "constitute an offer or solicitation."
        )
        keep, reason = selection.evaluate_chunk(chunk)
        assert not keep
        assert reason == "boilerplate:disclaimer"

    def test_directors_report_formalities(self):
        chunk = _chunk(
            "Appointment and Remuneration. The Board recommends the "
            "appointment of Mr. A. Sharma as an Independent Director. "
            "The terms of office expire at the ensuing Annual General Meeting. "
            "A declaration of independence has been received from him."
        )
        keep, reason = selection.evaluate_chunk(chunk)
        assert not keep
        assert reason == "boilerplate:directors_report_formalities"

    def test_long_legal_section(self):
        chunk = _chunk(
            "Nothing contained herein shall be binding upon the Company, "
            "save as provided under the Articles. The provisions shall be "
            "construed in accordance with the rules and regulations framed "
            "pursuant to Section 134 of the Companies Act, 2013, and shall "
            "remain in force."
        )
        keep, reason = selection.evaluate_chunk(chunk)
        assert not keep
        assert reason == "boilerplate:legal_section"

    def test_empty_chunk(self):
        keep, reason = selection.evaluate_chunk(_chunk(""))
        assert not keep
        assert reason == "empty"


# ---------------------------------------------------------------------------
# Generic keep/drop rules
# ---------------------------------------------------------------------------
class TestGenericRules:
    def test_revenue_note_section_beats_number_rule(self):
        chunk = _chunk(
            "Revenue from operations for the year ended March 31, 2024 was "
            "Rs 14,512 crore against Rs 13,641 crore in the previous year.",
            source_doc="Q4_FY24_results.pdf", doc_type="quarterly",
        )
        keep, reason = selection.evaluate_chunk(chunk)
        # "revenue from operations" is itself a strong section signal.
        assert keep
        assert reason == "section:revenue_note"

    def test_transcript_qualitative_commentary_kept(self):
        # No digit at all, but transcripts discuss margins in words.
        chunk = _chunk(
            "We expect gross margins to remain under pressure in the first "
            "half on account of crude-linked input costs, with a recovery "
            "thereafter as price realisations kick in.",
            doc_type="transcript", source_doc="Q4_FY24_earnings_call.pdf",
        )
        keep, reason = selection.evaluate_chunk(chunk)
        assert keep
        assert reason == "doc_type:qualitative_commentary"

    def test_kpi_word_and_number_kept_outside_strong_sections(self):
        chunk = _chunk(
            "Distribution reached 9.2 lakh outlets during FY24, up from "
            "8.6 lakh the prior year. Modern trade grew 14%.",
            source_doc="investor_presentation.pdf", doc_type="presentation",
        )
        keep, reason = selection.evaluate_chunk(chunk)
        assert keep
        assert reason in ("doc_type:qualitative_commentary", "kpi_signal")

    def test_unrelated_text_skipped(self):
        chunk = _chunk(
            "The Secretarial Standards Committee met to review the minutes "
            "of the previous meetings held during the year."
        )
        keep, reason = selection.evaluate_chunk(chunk)
        assert not keep
        assert reason == "no_kpi_signal"


# ---------------------------------------------------------------------------
# select_kpi_chunks: stats, order, page preservation, enabled switch
# ---------------------------------------------------------------------------
class TestSelectKpiChunks:
    CHUNKS = [
        _chunk("Financial Highlights. PAT Rs 1,140 crore, up 8%.", page=6),
        _chunk("Table of Contents\nResults 48\nReport 52", page=2),
        _chunk("Segment Information. Bakery revenue Rs 12,100 crore.", page=101),
        _chunk("Annual Report 2023-24", page=1),
    ]

    def test_kept_and_skipped_counts(self):
        selected, stats = selection.select_kpi_chunks(self.CHUNKS)
        assert stats["total"] == 4
        assert stats["kept"] == 2
        assert stats["skipped"] == 2
        assert stats["kept"] == len(selected)

    def test_input_order_preserved(self):
        selected, _ = selection.select_kpi_chunks(self.CHUNKS)
        assert [c["page"] for c in selected] == [6, 101]

    def test_page_numbers_untouched(self):
        # The filter must never renumber or rewrite chunks: what goes in is
        # the same object that comes out, so citations keep real pages.
        selected, _ = selection.select_kpi_chunks(self.CHUNKS)
        for out in selected:
            original = next(c for c in self.CHUNKS if c["chunk_id"] == out["chunk_id"])
            assert out is original
            assert out["page"] == original["page"]

    def test_skip_reasons_are_reported(self):
        _, stats = selection.select_kpi_chunks(self.CHUNKS)
        assert stats["skip_reasons"]["boilerplate:table_of_contents"] == 1
        assert stats["skip_reasons"]["boilerplate:cover"] == 1

    def test_kept_by_doc_reported(self):
        _, stats = selection.select_kpi_chunks(self.CHUNKS)
        assert stats["kept_by_doc"]["annual_report.pdf"] == 2

    def test_disabled_returns_everything(self):
        selected, stats = selection.select_kpi_chunks(self.CHUNKS, enabled=False)
        assert len(selected) == 4
        assert stats["skipped"] == 0

    def test_empty_input(self):
        selected, stats = selection.select_kpi_chunks([])
        assert selected == []
        assert stats["total"] == 0

    def test_format_stats_is_one_line(self):
        _, stats = selection.select_kpi_chunks(self.CHUNKS)
        line = selection.format_stats(stats)
        assert "\n" not in line
        assert "kept 2/4" in line