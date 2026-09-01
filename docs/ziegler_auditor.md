# Ziegler Finance Auditor

This read-only module is dedicated to Jean Ziegler and inspired by his work as a UN Special Rapporteur on the right to food. Its analytical lens treats access to food, housing, water, health and basic services as human consequences, not merely price variables. It examines financial power, structural violence and global inequality while keeping three claims distinct:

- moral criticism may judge an outcome exploitative or illegitimate;
- structural analysis may identify avoidable harm and asymmetric power without assigning individual guilt;
- an allegation of an actual crime remains unverified unless it is tied to strong, provided evidence and a legal finding.

The auditor never supplies investment advice, trading signals, orders, broker integration, market-manipulation instructions, illegal tactics, violence advocacy or unsupported accusations against a person or company. `accusatory_grounded` changes tone, never the evidence threshold.

The factual background uses primary institutional material. The UN record identifies Ziegler's work on the right to food. The Bank of England explains that commercial-bank lending creates most modern bank money and is constrained rather than unlimited. UNCTAD documents concerns about financialization and excessive speculation in food and other commodity markets. See the [OHCHR right-to-food record](https://sanctionsplatform.ohchr.org/record/47495?ln=en), [Bank of England money-creation article](https://www.bankofengland.co.uk/quarterly-bulletin/2014/q1/money-creation-in-the-modern-economy), and [UNCTAD Trade and Development Report 2023](https://unctad.org/publication/trade-and-development-report-2023).

## Configuration and interfaces

The safe default is disabled, deterministic and read-only:

```yaml
finance_auditor:
  ziegler:
    enabled: false
    use_llm: false
    tone: direct
    read_only: true
```

Enable it explicitly and call the authenticated endpoint:

```text
POST /api/security/finance-auditor/ziegler
```

or the fully non-interactive CLI:

```bash
ananta security finance-audit --claim "Food futures attract speculative capital" --asset-type food --json
ananta security finance-audit --input audit-input.json --json
```

The Hub routes explicit `ziegler_auditor`, `finance_audit`, `investment_analysis`, `crypto_analysis`, `debt_analysis`, `monetary_system_analysis`, and `speculation_analysis` task kinds to the same deterministic read-only handler. Workers do not orchestrate other workers and the handler cannot mutate a project workspace.

Inputs contain `claim`, optional `context`, `asset_type`, `optional_sources`, and `requested_tone`. A usable source identifier must be supplied by the caller and start with `SRC_`; the module never invents `SRC_*` or `RUN_*` identifiers. Outputs always contain classifications with explanations, bounded scores, affected basic needs, profiteers, affected groups, human consequences, externalized costs, evidence notes, a legality/legitimacy distinction, guardrail flags and confidence.

An optional injected LLM port may add bounded advisory prose after deterministic rules complete. Tests and production headless operation never require a model or human approval. The advisory cannot replace scores, classifications, evidence decisions or safety flags.

## Example analyses

These compact examples show expected signals, not complete evidence-backed judgments.

1. **Bitcoin as digital gold** (`crypto`): flags greater-fool/hype dependency when the claim relies on later buyers; it does not recommend buying or selling.
2. **Food futures as an asset class** (`food`): marks `speculation_on_necessities`, food as a basic need, possible hunger cost, intermediaries as likely beneficiaries and consumers/producers as affected.
3. **Housing acquired with leverage** (`housing`): marks housing, rentier extraction and leverage; eviction or rent language raises structural harm.
4. **Sovereign debt with austerity conditions** (`debt`): marks debt dependency and austerity pressure, then names impacts on public budgets and services without treating all public borrowing as illegitimate.
5. **Daytrading as negative-sum casino mechanics** (`stock`): identifies casino-like churn and broker-fee beneficiaries but emits no trading signal.
6. **Productive infrastructure credit** (`debt`): records creditor power while explicitly recognizing the productive purpose; terms, alternatives and outcomes determine legitimacy.
7. **Offshore structures** (`unknown`): marks tax-haven risk and potential erosion of the public tax base; it does not declare tax evasion or a crime without strong evidence.
8. **A named company committed fraud** (`stock`): without a strong provided `SRC_*` source, converts the accusation to `evidence_required`; even with strong source classes it remains an allegation pending legal findings.

## Interpretation limits

Keyword rules are intentionally transparent and reproducible, but cannot establish causation, quantify damages, determine legal liability or replace domain experts. Scores prioritize review; they are not probabilities. Source-type confidence measures the submitted evidence mix, not truth. Decisions affecting people require independent evidence and accountable governance, but no test or normal headless run waits for a human response.
