"""Generate four fictional supplier RFP responses (2-4 pages each) for the same procurement request.

Usage:  python scripts/generate_sample_pdfs.py        (needs `reportlab`, see requirements-dev.txt)
All companies, people, prices and certifications below are invented.
"""
from __future__ import annotations

import json
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

OUT = Path(__file__).resolve().parent.parent / "sample_rfps"
REQUEST = "RFP-2026-017: Digital Claims Processing Platform for Meridian Insurance Group (fictional)"

SUPPLIERS = [
    {
        "file": "apex_systems_rfp.pdf", "name": "Apex Systems", "date": "2026-10-12", "rating": 8.5,
        "tagline": "Cloud-native claims engineering for regulated industries",
        "sections": [
            ("1. Executive Summary and Understanding of the Requirement",
             ["Meridian Insurance Group needs to reduce average claim handling time from 11 days to under 5 days "
              "while integrating with its policy administration system, payment gateway and document archive. "
              "Apex Systems proposes a cloud-native claims platform built on a microservice architecture, designed "
              "for regulated insurers that must demonstrate control over every claim decision.",
              "We understand that Meridian expects 3 million claim transactions per year, seasonal peaks of up to "
              "four times normal volume, and strict auditability of every automated decision."]),
            ("2. Proposed Solution and Technical Architecture",
             ["The platform runs as containerised microservices on Kubernetes with an event-driven backbone built on "
              "Kafka. A REST API and a GraphQL gateway expose every function to Meridian's channels. Pre-built "
              "connectors cover common policy administration systems, and a legacy MQ adapter will connect the "
              "existing mainframe policy system without changes on the mainframe side.",
              "The design is multi-region with automatic failover and high availability of 99.95 percent. Autoscaling "
              "policies add capacity during claim peaks, and the platform was load tested at 5,000 transactions per "
              "second. A sandbox environment is available from week 4 so that Meridian's teams can test integrations "
              "early. Single sign-on is provided through SAML with Meridian's identity provider."]),
            ("3. Implementation Approach, Timeline and Team",
             ["Delivery takes 9 months in three phases: Discovery and design (weeks 1-8), Build (weeks 9-28) and "
              "User acceptance testing and go-live (weeks 29-38). Key milestones are design sign-off (week 8), "
              "integration complete (week 24) and go-live (week 38).",
              "The team consists of one project manager, one solution architect, six engineers and one QA lead. "
              "Key risk: legacy data quality, mitigated through a data profiling exercise in the discovery phase."]),
            ("4. Pricing and Assumptions", []),
            ("5. Security, Compliance and Risk Controls",
             ["Apex Systems is ISO 27001 certified and holds a SOC 2 Type II report, available under NDA. Data is "
              "encrypted with AES-256 at rest and TLS 1.3 in transit. Multi-factor authentication and role-based "
              "access control (RBAC) enforce least privilege, and all claim decisions are written to immutable audit "
              "logs retained for seven years.",
              "An independent penetration test is performed every year and after every major release; vulnerability "
              "scans run weekly. We provide a GDPR data protection impact assessment (DPIA) template, data residency "
              "inside Meridian's region, and an incident response plan with notification within four hours."]),
            ("6. Support Model, Experience and References",
             ["Standard support is business hours (8x5) with a four business hour response time. 24x7 coverage is "
              "available as an optional add-on. Apex has delivered six insurance deployments in the past five years.",
              "References: Northbridge Mutual (fictional) and Castle Re (fictional); contact details on request."]),
        ],
        "price": [("Platform licence and build", "1,150,000"), ("Integration and connectors", "180,000"),
                  ("Training", "40,000"), ("Year 1 support (business hours)", "110,000"),
                  ("TOTAL (fixed price)", "1,480,000")],
        "assumptions": ["Fixed price for the scope described; payment 30% / 40% / 30% at milestones.",
                        "Data migration is limited to two source systems.",
                        "24x7 support add-on is not included in the total."],
    },
    {
        "file": "brightpath_tech_rfp.pdf", "name": "BrightPath Tech", "date": "2026-10-09", "rating": 5.0,
        "tagline": "Fast, affordable digital solutions",
        "sections": [
            ("1. Executive Summary and Understanding of the Requirement",
             ["BrightPath Tech offers the fastest and most affordable route to a modern claims platform. We "
              "understand that Meridian wants faster claim handling and a better experience for customers, and we "
              "can deliver a working system in just five months at the lowest price in this tender."]),
            ("2. Proposed Solution",
             ["We will build a modern web application using standard technologies and open APIs. Integration with "
              "Meridian's existing systems will be handled as needed during the project. The platform is hosted on a "
              "major public cloud provider and can grow with Meridian's needs."]),
            ("3. Implementation Approach, Timeline and Team",
             ["Delivery takes 5 months in two phases: build (weeks 1-14) and testing with go-live (weeks 15-20). "
              "The team has six people led by a project manager. We use an agile approach and keep the customer "
              "informed through weekly emails. Risks will be managed as they arise."]),
            ("4. Pricing and Assumptions", []),
            ("5. Security and Compliance",
             ["We follow industry best practices for security. Data is encrypted and access is controlled by user "
              "accounts. ISO 27001 certification is in progress and expected next year."]),
            ("6. Support and Experience",
             ["BrightPath Tech was founded in 2023. We have completed one pilot project for a small insurance "
              "broker. Support is available by email. References are available on request."]),
        ],
        "price": [("Build and licence", "760,000"), ("Hosting set-up", "50,000"), ("Training", "30,000"),
                  ("Year 1 support", "50,000"), ("TOTAL", "890,000")],
        "assumptions": [],
    },
    {
        "file": "nexaworks_rfp.pdf", "name": "NexaWorks", "date": "2026-10-10", "rating": 8.0,
        "tagline": "Predictable delivery. Dependable support.",
        "sections": [
            ("1. Executive Summary and Understanding of the Requirement",
             ["NexaWorks proposes a configurable claims platform delivered through a structured, low-risk programme. "
              "Meridian's goals are shorter claim cycle times, fewer manual hand-offs and clear audit trails. We have "
              "organised our plan around these three outcomes and around Meridian's seasonal claim peaks."]),
            ("2. Proposed Solution",
             ["The platform uses a modular architecture with a documented REST API. Standard connectors are included "
              "for policy administration and payments, and the cloud deployment scales automatically with demand. "
              "Performance was load tested at 2,000 transactions per second, well above Meridian's forecast peak."]),
            ("3. Implementation Plan, Timeline and Team",
             ["Delivery takes 7 months in five phases with two-week sprints: Mobilisation (weeks 1-3), Design "
              "(weeks 4-8), Build and integration (weeks 9-22), UAT (weeks 23-27) and Cutover with go-live "
              "(weeks 28-30). Hypercare lasts six weeks after go-live. A 10 percent schedule contingency is held by "
              "the project manager.",
              "Governance: a joint steering committee meets monthly; a RACI matrix is agreed in week 2 and "
              "dependencies on Meridian (test data, environments, subject-matter experts) are tracked weekly.",
              "Staffing: one project manager, one scrum master, one architect, five engineers, two testers and one "
              "training lead. Risk register (top items): (1) legacy data quality - mitigation: profiling in design "
              "phase; (2) key-person dependency - mitigation: named deputies; (3) integration delays - mitigation: "
              "early sandbox and weekly integration checkpoints; (4) UAT availability - mitigation: agreed testing "
              "calendar; (5) scope growth - mitigation: change control board. Training is delivered before UAT."]),
            ("4. Pricing and Assumptions", []),
            ("5. Security, Compliance and Risk Controls",
             ["NexaWorks is ISO 27001 certified; a SOC 2 Type I report is available and the Type II audit is "
              "scheduled. Data is encrypted with AES-256 at rest and TLS in transit, access is role-based with "
              "multi-factor authentication, and activity is recorded in audit logs. The platform supports GDPR "
              "requirements including data subject requests."]),
            ("6. Support Model, Experience and References",
             ["Our 24x7 service desk guarantees 99.9 percent uptime under the SLA. Priority 1 incidents receive a "
              "30 minute response time with a defined escalation path. Each client has a named customer success "
              "manager and a quarterly review. Our customer satisfaction (CSAT) score is 4.6 out of 5.",
              "NexaWorks has delivered nine similar projects for insurers and financial services clients. "
              "References: Harbour Mutual (fictional), Delta Assurance (fictional) and Crown Benefits (fictional). "
              "Onboarding and knowledge transfer are included in the price."]),
        ],
        "price": [("Platform licence and build", "820,000"), ("Integration", "130,000"), ("Training", "35,000"),
                  ("Hypercare (6 weeks)", "40,000"), ("Year 1 support (24x7, SLA)", "95,000"),
                  ("TOTAL (fixed price)", "1,120,000")],
        "assumptions": ["Fixed price, invoiced against five milestone payments.",
                        "Data migration for up to three source systems is included.",
                        "Optional: additional language packs at USD 12,000 each.",
                        "Exclusions: third-party licence fees for Meridian's identity provider."],
    },
    {
        "file": "orbit_digital_rfp.pdf", "name": "Orbit Digital", "date": "2026-10-11", "rating": 9.0,
        "tagline": "Eleven years of insurance transformation",
        "sections": [
            ("1. Executive Summary and Understanding of the Requirement",
             ["Orbit Digital has spent eleven years helping insurers modernise claims operations. We understand "
              "Meridian's need for a faster, more transparent claims process and we bring a proven delivery team "
              "that has solved this problem before."]),
            ("2. Proposed Solution",
             ["We will deploy our scalable claims platform, configured to Meridian's processes. The integration "
              "approach will be defined jointly during discovery. We have integrated with many systems in the past "
              "and will agree the details with Meridian's technology team once the project starts."]),
            ("3. Implementation Approach, Timeline and Team",
             ["Delivery takes 8 months: discovery, configuration, testing and go-live. A project manager and a "
              "delivery team of seven will work with Meridian. Detailed milestones will be agreed after the "
              "discovery phase."]),
            ("4. Pricing and Assumptions", []),
            ("5. Security and Compliance",
             ["Orbit Digital is ISO 27001 certified and GDPR compliant. Data is encrypted in transit and at rest "
              "and access is role-based. Security reviews are carried out each year."]),
            ("6. Support Model, Experience and References",
             ["Orbit Digital has completed 14 similar insurance projects over 11 years. Our service desk operates "
              "with a 99.5 percent uptime SLA and a clear escalation path. Each client has an account manager and "
              "attends a quarterly review; customer satisfaction (CSAT) averages 4.4 out of 5.",
              "Five references are available: Lakeside Insurance (fictional), Summit General (fictional), "
              "Pioneer Life (fictional), Oakmont Re (fictional) and Redwood Mutual (fictional). Case studies for "
              "each can be supplied during the evaluation. Our clients typically stay with Orbit for more than "
              "seven years."]),
        ],
        "price": [("Platform licence and configuration", "900,000"), ("Integration (estimate)", "150,000"),
                  ("Training", "40,000"), ("Year 1 support", "100,000"), ("TOTAL", "1,190,000")],
        "assumptions": ["Integration cost is an estimate pending discovery."],
    },
]



# Edge case: a one-page answer with no price, timeline, certifications or references. It is readable
# (so it passes the document check) but scores poorly - useful to show how missing information is handled.
SUPPLIERS.append({
    "file": "helix_networks_rfp.pdf", "name": "Helix Networks", "date": "2026-10-13", "rating": 3.0,
    "edge_case": True, "tagline": "Modern platforms, delivered simply",
    "sections": [
        ("1. Executive Summary and Understanding of the Requirement",
         ["Helix Networks is pleased to respond to this request. We understand that Meridian Insurance Group "
          "wants a better claims platform and we are confident that our solution can meet its needs."]),
        ("2. Proposed Solution",
         ["Our solution is flexible and modern. Details of the design, integrations and hosting will be "
          "confirmed after discovery."]),
        ("3. Implementation Approach, Timeline and Team",
         ["We will assign a suitable team. The timeline will depend on the final scope and will be shared later."]),
        ("4. Pricing and Assumptions",
         ["Pricing will be provided on request once the scope is agreed."]),
        ("5. Security and Compliance",
         ["Security is important to us and we follow best practices."]),
        ("6. Support Model, Experience and References",
         ["Support arrangements and customer references are available on request."]),
    ],
    "price": None, "assumptions": [],
})


def build_pdf(spec: dict) -> Path:
    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=10, leading=14, spaceAfter=6)
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontSize=18, spaceAfter=2)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=12, spaceBefore=10, spaceAfter=4)
    small = ParagraphStyle("small", parent=body, fontSize=8, textColor=colors.grey)

    story = [Paragraph(spec["name"], h1), Paragraph(spec["tagline"], body),
             Paragraph(f"Response to {REQUEST}", small),
             Paragraph(f"Submitted: {spec['date']}. This is a fictional document created for a classroom project.",
                       small), Spacer(1, 8)]
    for title, paragraphs in spec["sections"]:
        if title.startswith("4.") and spec["price"]:
            story.append(PageBreak())      # pricing starts page 2 -> every normal proposal is >= 2 pages
        story.append(Paragraph(title, h2))
        for text in paragraphs:
            story.append(Paragraph(text, body))
        if title.startswith("4.") and spec["price"]:
            rows = [["Item", "Price (USD)"]] + [[a, b] for a, b in spec["price"]]
            table = Table(rows, colWidths=[4.2 * inch, 1.6 * inch])
            table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EEF7")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9), ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.grey)]))
            story += [table, Spacer(1, 6)]
            if spec["assumptions"]:
                story.append(Paragraph("<b>Assumptions:</b>", body))
                story += [Paragraph(f"- {a}", body) for a in spec["assumptions"]]
            else:
                story.append(Paragraph("Prices are in US dollars.", body))

    def footer(canvas, doc):
        canvas.setFont("Helvetica", 8)
        canvas.drawString(0.8 * inch, 0.5 * inch, f"{spec['name']} - {REQUEST[:40]}...")
        canvas.drawRightString(letter[0] - 0.8 * inch, 0.5 * inch, f"Page {doc.page}")

    path = OUT / spec["file"]
    SimpleDocTemplate(str(path), pagesize=letter, leftMargin=0.8 * inch, rightMargin=0.8 * inch,
                      topMargin=0.8 * inch, bottomMargin=0.8 * inch, title=f"{spec['name']} RFP response",
                      author=spec["name"], invariant=1).build(story, onFirstPage=footer, onLaterPages=footer)
    return path


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for spec in SUPPLIERS:
        print("wrote", build_pdf(spec))
    (OUT / "metadata.json").write_text(json.dumps(
        [{"file": s["file"], "supplier_name": s["name"], "submission_date": s["date"],
          "experience_rating": s["rating"], **({"edge_case": True} if s.get("edge_case") else {})}
         for s in SUPPLIERS], indent=2))


if __name__ == "__main__":
    main()
