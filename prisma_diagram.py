# prisma_diagram.py
# Generates a real PRISMA 2020 flow diagram from your screening numbers and
# opens it in your browser.
import os
import webbrowser
from ai import ask_json

OUTPUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prisma_flow.html")

TEMPLATE = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>PRISMA Flow Diagram</title>
<style>
  body {{ font-family: Arial, sans-serif; background: #fff; padding: 30px; }}
  .box {{ border: 2px solid #333; border-radius: 6px; padding: 12px 16px; margin: 10px auto; width: 340px; text-align: center; }}
  .arrow {{ text-align: center; font-size: 22px; margin: 0; }}
  .row {{ display: flex; justify-content: center; align-items: flex-start; gap: 40px; }}
</style></head>
<body>
<h2 style="text-align:center;">PRISMA 2020 Flow Diagram</h2>

<div class="box"><b>Identification</b><br>Records identified: {identified}</div>
<div class="arrow">&#8595;</div>
<div class="row">
  <div class="box"><b>Screening</b><br>Records screened: {screened}</div>
  <div class="box"><b>Excluded</b><br>Records excluded: {excluded_screening}</div>
</div>
<div class="arrow">&#8595;</div>
<div class="row">
  <div class="box"><b>Eligibility</b><br>Full-text assessed: {full_text_assessed}</div>
  <div class="box"><b>Excluded</b><br>Full-text excluded: {excluded_eligibility}</div>
</div>
<div class="arrow">&#8595;</div>
<div class="box"><b>Included</b><br>Studies included: {included}</div>

</body></html>"""

PARSE_PROMPT = """
Extract PRISMA flow diagram numbers from this description. Respond ONLY
with JSON (use null for anything not mentioned):
{
  "identified": number or null,
  "screened": number or null,
  "excluded_screening": number or null,
  "full_text_assessed": number or null,
  "excluded_eligibility": number or null,
  "included": number or null
}
"""


def parse_prisma_numbers(instruction):
    messages = [
        {"role": "system", "content": PARSE_PROMPT},
        {"role": "user", "content": instruction},
    ]
    numbers = ask_json(messages, default={})
    return numbers if isinstance(numbers, dict) else {}


def generate_prisma_flow(numbers):
    def _val(key):
        # A real count of 0 is valid - only a missing value becomes "?".
        v = numbers.get(key)
        return "?" if v is None else v

    html = TEMPLATE.format(**{k: _val(k) for k in (
        "identified", "screened", "excluded_screening",
        "full_text_assessed", "excluded_eligibility", "included",
    )})
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    webbrowser.open(f"file://{OUTPUT_PATH}")
    return OUTPUT_PATH


def handle_prisma_request(instruction):
    numbers = parse_prisma_numbers(instruction)
    path = generate_prisma_flow(numbers)
    return f"Generated your PRISMA flow diagram and opened it in your browser."