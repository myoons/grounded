import re
import unittest
from pathlib import Path
from urllib.parse import unquote, urlsplit


class DocumentationTests(unittest.TestCase):
    def test_public_documentation_has_only_reviewed_product_documents(self):
        root = Path(__file__).resolve().parents[1]
        allowed = {
            "CAPACITY_POLICY.md",
            "CAPTURE_STATE.md",
            "CLAIM_LIFECYCLE_EVALUATION.md",
            "CLAIM_PROMPT_EVALUATION.md",
            "DESIGN_EXPERIMENTS.md",
            "DURABLE_AUDIO.md",
            "GPU_SPEECH.md",
            "HISTORY_SCHEMA.md",
            "MEETING_HISTORY.md",
            "OBSERVABILITY.md",
            "README.md",
            "RESEARCH_PIPELINE.md",
            "REVIEW_GUIDE.md",
            "RUNNING.md",
            "SEARCH_PAGE_FILTER.md",
            "SECURITY.md",
            "SYNTHESIS_GOLD_EVALUATION.md",
            "SYSTEM_GUIDE.md",
            "VALIDATION.md",
            "VISUAL_ATLAS.md",
        }
        self.assertEqual({path.name for path in (root / "docs").glob("*.md")}, allowed)

    def test_relative_documentation_links_resolve(self):
        root = Path(__file__).resolve().parents[1]
        documents = [root / "README.md", *sorted((root / "docs").glob("*.md"))]
        for document in documents:
            for destination in re.findall(
                r"\[[^\]]*\]\(([^)]+)\)", document.read_text()
            ):
                parsed = urlsplit(destination)
                if parsed.scheme or not parsed.path:
                    continue
                target = (document.parent / unquote(parsed.path)).resolve()
                with self.subTest(document=document.name, target=destination):
                    self.assertTrue(
                        target.is_relative_to(root), "Link escapes source repository"
                    )
                    self.assertTrue(target.is_file(), "Documentation link is broken")
