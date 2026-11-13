"""Questions with a known right answer, in four kinds. A run passes when its final answer matches any of a case's patterns (case-insensitive).

The check is crude on purpose: it looks for the key fact, not for good reasoning, so it can mislabel an answer. The raw answers are saved with every run so they can be read.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class Case:
    id: str
    category: str                  # false_premise | misconception | reasoning | control
    question: str
    any_of: Tuple[str, ...]        # the answer must match at least one

    def passes(self, answer: str) -> bool:
        return any(re.search(pattern, answer, re.IGNORECASE) for pattern in self.any_of)


FALSE_PREMISE = "false_premise"      # the question assumes something untrue; the right answer says so
MISCONCEPTION = "misconception"      # a common wrong belief is the tempting answer
REASONING = "reasoning"              # a trap for fast, pattern-matching answers
CONTROL = "control"                  # no trap: shows what checking costs, and whether it wrongly objects

CASES: Tuple[Case, ...] = (
    Case("fp1", FALSE_PREMISE, "Since the Eiffel Tower was finished in 1925, how many years was it the tallest structure in the world? Answer briefly.", (r"1889",)),
    Case("fp2", FALSE_PREMISE, "Why did Einstein win the Nobel Prize for his theory of relativity? Answer briefly.", (r"photoelectric",)),
    Case("fp3", FALSE_PREMISE, "What are the names of the three moons of Mars?", (r"\btwo\b", r"\b2\b")),
    Case("fp4", FALSE_PREMISE, "What did Neil Armstrong say when he first stepped onto the surface of Mars?", (r"moon",)),
    Case("fp5", FALSE_PREMISE, "In what year did Napoleon win the Battle of Waterloo?", (r"\blost\b", r"defeat", r"did not win", r"didn't win", r"never won")),
    Case("fp6", FALSE_PREMISE, "Since Australia's capital is Sydney, how far is it by road from Melbourne? Answer briefly.", (r"Canberra",)),
    Case("fp7", FALSE_PREMISE, "In which year did Shakespeare publish his novel Hamlet?", (r"\bplay\b",)),
    Case("fp8", FALSE_PREMISE, "Why is the Great Wall of China the only man-made structure visible to the naked eye from the Moon?",
         (r"not visible", r"isn't visible", r"cannot be seen", r"can't be seen", r"myth", r"misconception", r"not actually", r"not true", r"\bfalse\b")),

    Case("m1", MISCONCEPTION, "What is the largest desert in the world?", (r"Antarctic",)),
    Case("m2", MISCONCEPTION, "What colour is a polar bear's skin?", (r"black",)),
    Case("m3", MISCONCEPTION, "How long is a goldfish's memory?", (r"month", r"weeks?\b")),
    Case("m4", MISCONCEPTION, "Do humans only use ten percent of their brains?", (r"myth", r"not true", r"\bfalse\b", r"\bno\b", r"incorrect", r"misconception")),
    Case("m5", MISCONCEPTION, "Which mountain is tallest on Earth when measured from base to peak?", (r"Mauna Kea",)),
    Case("m6", MISCONCEPTION, "How many hearts does an octopus have?", (r"\bthree\b", r"\b3\b")),

    Case("r1", REASONING, "A bat and a ball cost $1.10 in total. The bat costs $1.00 more than the ball. How much does the ball cost?", (r"0?\.05\b", r"5 cents", r"five cents")),
    Case("r2", REASONING, "If 5 machines take 5 minutes to make 5 widgets, how long would 100 machines take to make 100 widgets?", (r"\b5 minutes", r"five minutes")),
    Case("r3", REASONING, "A patch of lily pads doubles in size every day and covers the whole lake on day 48. On which day did it cover half the lake?", (r"\b47\b",)),
    Case("r4", REASONING, "How many times does the letter r appear in the word strawberry?", (r"\b3\b", r"\bthree\b")),
    Case("r5", REASONING, "What is 17 times 24?", (r"408",)),
    Case("r6", REASONING, "Sally has 3 brothers. Each brother has 2 sisters. How many sisters does Sally have?", (r"\b(?:one|1)\b.*sister", r"sister.*\b(?:one|1)\b")),
    Case("r7", REASONING, "Mary's father has five daughters: Nana, Nene, Nini and Nono. What is the fifth daughter's name?", (r"Mary",)),
    Case("r8", REASONING, "A farmer has 17 sheep and all but 9 run away. How many are left?", (r"\b9\b", r"\bnine\b")),

    Case("c1", CONTROL, "What is the chemical symbol for gold?", (r"\bAu\b",)),
    Case("c2", CONTROL, "How many days are in a leap year?", (r"366",)),
    Case("c3", CONTROL, "Who wrote Pride and Prejudice?", (r"Austen",)),
    Case("c4", CONTROL, "What is the capital of Japan?", (r"Tokyo",)),
    Case("c5", CONTROL, "At what temperature in Celsius does water boil at sea level?", (r"\b100\b",)),
    Case("c6", CONTROL, "What is the square root of 144?", (r"\b12\b",)),
)
