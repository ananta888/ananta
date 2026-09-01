# Predatory Derivatives Auditor

This optional Ziegler extension distinguishes risk transfer from an unrelated wager on another party's harm. The moral analogy is insuring a neighbour's house: insurance on one's own house offsets an existing loss; a large payout on a neighbour's fire, without owning or depending on that house, creates a different interest structure. The analogy is a policy lens, not a claim that every derivative is insurance or that a position holder caused damage.

Hedging offsets an owned, owed, supplied, anticipated or operational exposure. Speculation takes directional price risk without establishing such an offset. Naked exposure lacks the referenced own interest; synthetic exposure reproduces economics through contracts and may be either hedging or speculative depending on the relation. Market-making inventory hedges are treated separately because a dealer may offset exposure created while supplying liquidity.

This distinction has regulatory precedents but the module does not issue legal opinions. The US CFTC describes bona fide commodity hedging as economically appropriate reduction of risks arising from actual or anticipated assets, liabilities or services. EU short-selling rules prohibit uncovered sovereign-debt short sales and naked sovereign CDS. BIS analysis documents how margin calls, counterparty links and fire sales can transmit OTC derivatives stress. See [CFTC position limits and bona fide hedging](https://www.cftc.gov/IndustryOversight/MarketSurveillance/SpeculativeLimits/index.htm), [ESMA short-selling and uncovered sovereign CDS rules](https://www.esma.europa.eu/esmas-activities/markets-and-infrastructure/short-selling), and [BIS analysis of OTC clearing risks](https://www.bis.org/publications/clearing-risks-otc-derivatives-markets-ccp-bank-nexus).

## Configuration and operation

```yaml
finance_auditor:
  ziegler:
    enabled: true
    use_llm: false
    read_only: true
  predatory_derivatives:
    enabled: true
```

The existing authenticated Ziegler endpoint and non-interactive CLI return `predatory_derivatives_analysis` for derivative, option, future, swap, CDS, short, synthetic, leverage or margin-call claims. With the submodule disabled, the field is `null` and the normal Ziegler analysis remains available. The task kinds `predatory_derivatives`, `derivatives_analysis` and `options_analysis` route through the Hub's existing read-only handler.

The module cannot construct or execute a position. It has no broker, exchange, wallet, payment, shell or project-write interface and never asks a person to approve a test. It reframes “how to build/execute” and manipulation language as risk analysis. A concrete claim of intent, fraud, manipulation or short-and-distort conduct yields `evidence_required`; structural profit incentives never prove intent.

## Hedge and casino comparisons

- A farmer hedging anticipated wheat production has an own commercial price risk; a fund with no food exposure betting on food crisis and hunger does not.
- An importer hedging contracted currency exposure differs from leveraged directional FX speculation.
- A bond owner buying credit protection differs from a naked CDS-like position whose payout depends on an unrelated debtor's default.
- A market maker offsetting inventory differs from a synthetic-only short designed as a pure price bet.
- A hospital hedging input costs differs from a naked payoff linked to healthcare distress.

## Financial arson and bets on another party's harm

“Financial arson” is a political metaphor for a structure in which a party can profit from damage it did not previously bear, especially where it might also influence financing, ratings, supply, information or policy. The service reports influence as `none`, `weak`, `medium`, `strong` or `unknown`; high profit exposure with unknown influence remains unknown. It does not infer motive.

The policy heuristic may recommend `require_underlying_interest`, `require_exchange_transparency`, `ban_naked_exposure` or `ban_predatory_structure`. Pure, leveraged and opaque wagers on food, water, housing, energy or healthcare receive particularly high scrutiny because gains can be coupled to denial of basic needs. The documented political position is that a regulator should be able to require a real underlying interest and prohibit naked damage bets. Legitimate producer, consumer, borrower and inventory hedges remain eligible for `allow` or proportionate `restrict` outcomes.

Scores are bounded, transparent review priorities—not probabilities, legal findings or proof of market effects. Product language alone cannot establish ownership, netting, collateral, intent or influence. Missing relations remain `unclear`; any real regulatory decision needs position-level data, governing law and identified evidence.
