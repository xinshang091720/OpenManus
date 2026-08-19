"""Run the project Revit IFC workflow from the command line."""

import argparse
import asyncio
import json

from app.revit.workflow import RevitIfcAssignmentWorkflow


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("rvt_file_path")
    parser.add_argument("--standard-id", type=int, default=109003)
    parser.add_argument("--clear-existing", action="store_true")
    args = parser.parse_args()
    report = await RevitIfcAssignmentWorkflow().run(**vars(args))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
