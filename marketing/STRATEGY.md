# lgtmaybe social strategy

How we get lgtmaybe in front of developers, modelled on what worked for CodeRabbit
and adapted for a solo, open-source, zero-budget project. This document is the
durable strategy; the repeatable work lives in [`LOOP_PLAYBOOK.md`](LOOP_PLAYBOOK.md)
and is executed on a cadence by an agent, with Matt doing the actual posting.

## What CodeRabbit did (and what transfers)

CodeRabbit grew bottom-up: a two-click GitHub App install, free for open source
(100k+ OSS projects), positioned as a layer on top of the workflow you already
have rather than a new tool to adopt. On top of that they built goodwill with a
$1M open-source sponsorship commitment, kept a steady blog cadence, and let a
large third-party ecosystem of "best AI code review tools" and "CodeRabbit
alternatives" comparison posts do their SEO for them.

What transfers to lgtmaybe:

- **Free is the wedge, so lead with it.** CodeRabbit is free *for OSS*; lgtmaybe
  is free for everyone because there's nothing to sell — MIT, bring your own
  model. The ollama path is a review pipeline at literally $0.
- **Low-friction install.** Their two-click install is our copy-paste workflow
  file. Every post should end with the smallest possible "try it" block.
- **Ride the comparison ecosystem.** Dozens of sites maintain "CodeRabbit
  alternatives" roundups. Getting lgtmaybe listed in them is free distribution
  with buyer-intent traffic. We can't buy placement, but most accept
  submissions or respond to a polite note.
- **Goodwill substitute.** No sponsorship budget, so the substitute is public
  dogfooding (lgtmaybe reviews its own PRs — the repo is the demo) and genuinely
  useful engagement in threads where people ask about AI code review.

What does *not* transfer: paid ads, sponsorships, a sales motion, launch PR.
Skip all of it.

## Positioning (the angles, ranked)

Every post leads with one of these. Never lead with "another AI code reviewer".

1. **No keys in your secrets.** Keyless OIDC/WIF auth on Bedrock, Vertex, and
   Azure. Nobody else leads with this and it's the strongest differentiator for
   anyone in a cloud shop.
2. **Your model, your infra, your data.** Provider-agnostic: seven hosted
   providers, ollama, or any OpenAI-compatible endpoint. Code never transits a
   vendor's SaaS. Strong for privacy-conscious teams, r/selfhosted, and
   r/LocalLLaMA.
3. **$0 reviews on local models.** ollama on your own hardware, no per-seat fee,
   no usage meter.
4. **Engineering depth.** The line-anchoring problem (LLMs miscount diff line
   numbers), prompt-injection defense against malicious PRs, secret redaction
   before egress, self-reflection to cut false positives. These make the best
   HN/lobste.rs material because they're interesting even to people who'll never
   install it.
5. **Honest about being "maybe".** The name is the pitch: it's a reviewer, not a
   gate. Self-aware beats overclaiming in developer channels.

## Channels, ranked by comment quality

Ranked by feedback quality, not raw reach (r/coding delivered 7.7K views and two
useless comments on a previous post — reach without feedback is a last resort).

| Channel | Use for | Cadence |
|---|---|---|
| Hacker News (Show HN, then engineering posts) | angles 4 and 1 | launch once, then only when a post is genuinely strong |
| lobste.rs | engineering posts | same posts as HN, tagged properly |
| r/LocalLLaMA, r/selfhosted | angle 3 | one post each, then comment-only |
| r/aws, r/devops | angle 1 (OIDC/Bedrock) | one post, then comment-only |
| r/ExperiencedDevs | judgement-led posts (what AI review is actually good for) | rare, only essay-grade material |
| dev.to | republished blog posts for SEO | mirror each blog post with canonical URL |
| coles.codes blog | the source of everything above | steady — the loop drafts these |
| X / LinkedIn | short-form pointers to the above | ride-along, never the primary |
| Comparison sites / awesome lists | passive SEO | one outreach pass, then check quarterly |

## Content pillars

1. **Engineering posts** (blog → HN/lobste.rs): line anchoring, prompt-injection
   defense, recursive hunk walking, prompt-cache shaping. One idea per post.
2. **Setup guides** (blog → dev.to → topical subreddit): keyless Bedrock review
   in one workflow file; $0 reviews with ollama; the openai-compatible escape
   hatch with llama.cpp.
3. **Comparison content** (docs site): an honest "lgtmaybe vs CodeRabbit" page —
   including where CodeRabbit is better (hosted, zero setup, IDE integration).
   Honest comparisons get linked; puff pieces don't.
4. **Dated survey posts**: "AI code review, late 2026" style — compounds in
   search and is re-writable every six months.

## Rules of engagement (non-negotiable)

- **Always disclose.** Every post and comment says "I built this" or "I'm the
  author". Astroturfing gets caught and the ban outlasts the project.
- **Value first.** In someone else's thread, answer their actual question;
  mention lgtmaybe only when it's the honest answer.
- **One venue, one post.** Never cross-post the same link to multiple subreddits
  in the same week. Each venue gets copy written for it.
- **Reply to everything on our own posts** except low-effort slop accusations
  (jokes and technical pushback get replies; one-word accounts don't).
- **Never overclaim.** No "catches all bugs", no benchmark claims without a
  published run. The evals harness exists; cite it or stay quiet.
- **The agent drafts, Matt posts.** Nothing is published from an automated
  account. All drafts land in `marketing/drafts/` for review.

## Metrics (checked each loop iteration)

- GitHub stars and forks (trend, not absolutes)
- PyPI downloads via pypistats.org
- New third-party mentions (roundups, alternatives pages, blog posts)
- Per-post: comment quality in the thread, and GA4 engagement time on the blog

A post that drives engaged readers beats one that drives views. Kill any venue
that produces bounces two posts in a row.
