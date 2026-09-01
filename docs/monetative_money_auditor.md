# Monetative Money Auditor

This optional extension adds monetary architecture to the human-consequence lens of the Ziegler Finance Auditor. It is inspired by Monetative and Joseph Huber's sovereign-money proposals, but does not endorse one reform school. Sovereign money broadly proposes moving issuance of generally accepted transaction money from commercial-bank balance sheets to a public or central-bank-adjacent monetary authority. Full-reserve/100% money and narrow banking overlap with that aim but differ in institutional design; MMT and Post-Keynesian credit theory are distinct analytical traditions rather than synonyms.

The descriptive baseline follows central-bank sources. The [European Central Bank](https://www.ecb.europa.eu/ecb-and-you/explainers/tell-me-more/html/what_is_money.en.html) distinguishes central-bank money—cash and bank reserves—from commercial-bank deposit money and explains that bank lending creates deposits. The [Deutsche Bundesbank](https://www.bundesbank.de/en/tasks/topics/how-money-is-created-667392) likewise rejects the simple savings-intermediary story while emphasizing constraints from funding, risk, regulation, borrowers and monetary policy. The [Bank of England](https://www.bankofengland.co.uk/quarterly-bulletin/2014/q1/money-creation-in-the-modern-economy) provides a compatible balance-sheet account. These mechanics do not by themselves settle the normative question of who should receive seigniorage, allocate credit or bear crisis losses.

## Boundaries and operation

The auditor analyzes observable laws, balance sheets, incentives, public backstops and institutional power. It rejects stories about a secret group controlling every bank or event. Criticism of bank-money privilege is not an allegation that individual bankers or central bankers commit crimes. Named criminal allegations still require strong, caller-provided `SRC_*` evidence.

The extension is read-only and produces neither investment advice nor trading, broker, payment or monetary-policy actions. It runs offline without a model or a person in the loop. Enable it independently beneath the enabled base auditor:

```yaml
finance_auditor:
  ziegler:
    enabled: true
    use_llm: false
    tone: direct
    read_only: true
  monetative:
    enabled: true
```

Use the existing authenticated endpoint or headless CLI. Claims containing money-creation, bank-money, central-bank-money, sovereign-money, seigniorage, public-debt, interest, inflation or CBDC concepts receive a `monetary_system_analysis`. With `monetative.enabled: false`, the same request returns the normal Ziegler analysis and `monetary_system_analysis: null`; no missing backend or workflow wait is introduced.

## Six example analyses

1. **“Banks only lend prior savings.”** Marks `savings_intermediary_misconception` and explains simultaneous loan/deposit balance-sheet expansion.
2. **“Banks create unlimited money.”** Marks `unlimited_creation_misconception` and names capital, liquidity, funding, risk, credit demand, regulation and monetary policy as constraints.
3. **“Mortgage credit drives an asset boom.”** Separates useful housing finance from credit allocation that may amplify property prices; beneficiaries and later buyers/renters are reported separately.
4. **“Sovereign money democratizes money.”** Presents possible public seigniorage and clearer mandates alongside transition, credit-availability, central-bank-power and political-allocation risks.
5. **“A CBDC ends bank money.”** Distinguishes a public digital-money option from the separate policy choice of restricting commercial-bank deposits.
6. **“A secret cabal controls all money.”** Refuses the conspiracy premise and reframes the audit around observable mandates, regulation, ownership, balance sheets and incentives.

## Ziegler + Monetative

The Ziegler layer asks who profits, who is harmed and whether basic needs carry the cost. The Monetative layer asks which monetary architecture allocates new purchasing power and distributes private returns, public guarantees and crisis liability. Together they connect money mechanics to human outcomes without collapsing factual description into political advocacy.

The `monetary_democracy_score` begins at 50 and applies explicit bounded factors for transparency, public control, private profit extraction, public crisis liability and distributional effects. It is a review heuristic, not a scientific measurement. Technical stability and democratic legitimacy remain separate. Every reform option includes potential benefits and material criticisms; sovereign money is never represented as an automatic cure for inequality, inflation, financial cycles or capitalism.
