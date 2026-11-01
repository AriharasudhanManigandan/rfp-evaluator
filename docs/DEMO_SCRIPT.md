# Demonstration script (about 4 minutes)

Record the screen with the deployed Streamlit Cloud URL open. If you have an API key, pick the provider in the
sidebar; otherwise use **Offline demo** and say so.

## 1. Criteria from SQLite (30 s)
* Open **1 · Criteria**. Point out: 5 active criteria, weights total 100%, max score 10.
* Change *Technical Capability* weight to 40 and show the error that the weights must total 100% and the
  disabled **Save criteria** button. Change it back to 30.

## 2. Input and input validation (45 s) - error case #1
* Open **2 · Suppliers & Evaluate** and switch on **Use the 5 bundled synthetic RFP PDFs** (or upload the files from
  `sample_rfps/`).
* Rename *BrightPath Tech* to *Apex Systems*: the duplicate-name error appears and **Evaluate suppliers** is
  blocked. Set a submission date in the future to show the date error. Undo both.
* Open **Document check** to show characters, pages, extractor (PyMuPDF) and SHA-256 per file. Optional: upload a
  non-PDF renamed to `.pdf` to show "not a valid PDF file".

## 3. Successful run (60 s)
* Click **Evaluate suppliers**. Show the progress messages (one evaluation per supplier, then scoring and ranking).
* **3 · Leaderboard:** rank, absolute score, PPI, date, experience, and the "Why this position" column.
* **4 · Scorecards:** pick NexaWorks. Show score, benchmark, gap, relative %, weight, then open a criterion
  expander to show the evidence quote and justification. Show **Helix Networks** (the one-page edge case): low
  scores with "no supporting evidence" flags.
* **5 · Run details:** RFP_RUN_ID, tie-break rules and explanations, benchmarks, agent trace, formulas and the
  criteria snapshot. Click **Download complete result (JSON)**.

## 4. Validation case (45 s) - error case #2
* Back on tab 2, open **Validation demo - simulate a malformed LLM reply**, choose *NexaWorks*, and evaluate again.
* On **5 · Run details**, show the warnings: name mismatch corrected, score 15 clipped to 10, "eight" set to 0,
  unknown criterion 999 ignored, missing criterion imputed 0, missing evidence.
* Note that the run still completed (status `COMPLETED_WITH_WARNINGS`) and that NexaWorks ranks lower because of
  the imputed zeros. Every change is traceable.

## 5. Persistence (20 s)
* Open **History**. Both runs are stored in SQLite, each with its own run id. Load the first run again.

Close with: "The LLM only produced the scorecards. Every number on the leaderboard comes from the formulas and
tie-break rules shown in Run details."
