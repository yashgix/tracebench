"""Run the protected checks against the shop tree named by argv[1] and print the result as JSON."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, sys.argv[1])
here = Path(__file__).resolve().parent
suite = unittest.defaultTestLoader.discover(str(here), pattern="check_*.py", top_level_dir=str(here))
result = unittest.TestResult()
suite.run(result)
print(json.dumps({"run": result.testsRun, "failed": sorted(test.id() for test, _ in result.failures + result.errors)}))
