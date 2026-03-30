# ACC 2321 Learning Page Snippets

Updated March 27, 2026.

This file contains combined HTML blocks for Canvas Learning Activities pages.

These snippets now follow the Sinclair Learning Activities template more closely:

- use the same `View`, `Read`, and `Additional Resources` section pattern as the template
- use the same icon assets already present in course `16866`
- keep citations inline with the linked resource instead of relying on a separate note elsewhere

I started with Chapter 10 because it has the richest recoverable citation set from the D2L export.

## Recommended Setup

- Page title: use `Learning Activities` if you want to match the established ACC 2321 module pattern from the earlier migrated shell.
- Alternative title: use `Module X: Learning Activities` only if you decide you want every page title to carry its own module number for instructor-side findability.
- If the page content does not begin with a template-style title header and icon, start the page body with:
  `<hr style="border-top: 10px solid #AC1A2F; border-bottom: none; margin: 0 0 16px 0;" />`
- Best fit for ACC 2321: keep the Cengage launch item separate from the Learning Activities page.
- Recommended module flow:
  1. `Introduction and Checklist`
  2. `Learning Activities`
  3. any supporting file/page items
  4. `ASP, Homework, Tax Problem & Quiz | XX`
- Why keep Cengage separate:
  - the Learning Activities page reads better as a content-and-resources page
  - the Cengage item is an action/launch item, not course content
  - this matches the prior ACC 2321 shell pattern
  - it avoids duplicating the same launch path both in the page body and in the module list
- If the Cengage launch is a module `External URL` item, prefer `Load in a new tab` over iframe embedding unless your team has a reason to preserve same-tab behavior.

## Chapter 1

Suggested use:

- Paste this into the Chapter 1 Learning Activities page.
- This chapter has one video and one reading.

```html
<hr style="border-top: 10px solid #AC1A2F; border-bottom: none; margin: 0 0 16px 0;" />
<h2><img role="presentation" src="https://sinclair.instructure.com/courses/16866/files/498556/preview?verifier=TdMt16m17rIJ4MEpwx8CF2FSvXgr9cQiG7XQzKGW" alt="" width="45" data-decorative="true" loading="lazy"><span style="color: #ac1a2f;"><strong>View</strong></span></h2>
<h3><a href="https://www.youtube.com/watch?v=7Qtr_vA3Prw" target="_blank" rel="noopener">Taxes: Crash Course Economics #31</a></h3>
<p><strong>Citation:</strong> Crash Course. (2016, April 27). <em>All About Taxes</em>. Retrieved from YouTube: https://www.youtube.com/watch?v=7Qtr_vA3Prw</p>
<hr>
<h2><strong><span style="color: #ac1a2f;"><img role="presentation" src="https://sinclair.instructure.com/courses/16866/files/498526/preview?verifier=1M8QHy00HYy8iuXE9FstTpMQ6EWOZ3nu70xYyaOs" alt="" width="45" data-decorative="true" loading="lazy">Read</span></strong></h2>
<h3><a href="https://www.brookings.edu/articles/reducing-tax-rates-by-reducing-tax-bias/" target="_blank" rel="noopener">Reducing tax rates by reducing tax bias</a></h3>
<p><strong>Citation:</strong> Pozen, R. (2015, June 23). <em>Reducing Tax Rates by Reducing Tax Bias</em>. Retrieved from Brookings: https://www.brookings.edu/articles/reducing-tax-rates-by-reducing-tax-bias/</p>
<hr style="border-top: 8px solid #AC1A2F;">
```

## Chapter 2

Suggested use:

- Paste this into the Chapter 2 Learning Activities page.

```html
<hr style="border-top: 10px solid #AC1A2F; border-bottom: none; margin: 0 0 16px 0;" />
<h2><strong><span style="color: #ac1a2f;"><img role="presentation" src="https://sinclair.instructure.com/courses/16866/files/498526/preview?verifier=1M8QHy00HYy8iuXE9FstTpMQ6EWOZ3nu70xYyaOs" alt="" width="45" data-decorative="true" loading="lazy">Read</span></strong></h2>
<h3><a href="https://blogs.worldbank.org/developmenttalk/gender-differences-taxation-why-do-they-matter" target="_blank" rel="noopener">Gender differences in taxation: Why do they matter?</a></h3>
<p><strong>Citation:</strong> Grown, C., Ozer, C., &amp; Bronchi, C. (2022, July 20). <em>Gender Difference in Taxation: Why Do They Matter?</em> Retrieved from World Bank Blogs: https://blogs.worldbank.org/developmenttalk/gender-differences-taxation-why-do-they-matter</p>
<hr style="border-top: 8px solid #AC1A2F;">
```

## Chapter 3

Suggested use:

- Paste this into the Chapter 3 Learning Activities page.

```html
<hr style="border-top: 10px solid #AC1A2F; border-bottom: none; margin: 0 0 16px 0;" />
<h2><strong><span style="color: #ac1a2f;"><img role="presentation" src="https://sinclair.instructure.com/courses/16866/files/498526/preview?verifier=1M8QHy00HYy8iuXE9FstTpMQ6EWOZ3nu70xYyaOs" alt="" width="45" data-decorative="true" loading="lazy">Read</span></strong></h2>
<h3><a href="https://equitablegrowth.org/how-inequities-in-u-s-taxation-can-perpetuate-systemic-racism/" target="_blank" rel="noopener">How inequities in U.S. taxation can perpetuate systemic racism</a></h3>
<p><strong>Citation:</strong> Harrison, S. (2021, April 20). <em>Washington Center for Equitable Growth</em>. Retrieved from Equitable Growth: https://equitablegrowth.org/how-inequities-in-u-s-taxation-can-perpetuate-systemic-racism/</p>
<h3><a href="https://www.investopedia.com/terms/k/kiddietax.asp" target="_blank" rel="noopener">Kiddie Tax Discussion</a></h3>
<p><strong>Citation:</strong> Kagan, J. (2025, September 10). <em>Understanding Kiddie Tax: Rules and Rates</em>. Retrieved from Investopedia: https://www.investopedia.com/terms/k/kiddietax.asp</p>
<hr style="border-top: 8px solid #AC1A2F;">
```

## Chapter 4

Suggested use:

- Paste this into the Chapter 4 Learning Activities page.

```html
<hr style="border-top: 10px solid #AC1A2F; border-bottom: none; margin: 0 0 16px 0;" />
<h2><strong><span style="color: #ac1a2f;"><img role="presentation" src="https://sinclair.instructure.com/courses/16866/files/498526/preview?verifier=1M8QHy00HYy8iuXE9FstTpMQ6EWOZ3nu70xYyaOs" alt="" width="45" data-decorative="true" loading="lazy">Read</span></strong></h2>
<h3><a href="https://www.investopedia.com/terms/p/progressivetax.asp" target="_blank" rel="noopener">What Is a Progressive Tax? Advantages and Disadvantages</a></h3>
<p><strong>Citation:</strong> Kagan, J. (2023, March 04). <em>What Is a Progressive Tax? Advantages and Disadvantages</em>. Retrieved from Investopedia: https://www.investopedia.com/terms/p/progressivetax.asp</p>
<hr style="border-top: 8px solid #AC1A2F;">
```

## Chapter 8

Suggested use:

- Paste this into the Chapter 8 Learning Activities page.

```html
<hr style="border-top: 10px solid #AC1A2F; border-bottom: none; margin: 0 0 16px 0;" />
<h2><strong><span style="color: #ac1a2f;"><img role="presentation" src="https://sinclair.instructure.com/courses/16866/files/498526/preview?verifier=1M8QHy00HYy8iuXE9FstTpMQ6EWOZ3nu70xYyaOs" alt="" width="45" data-decorative="true" loading="lazy">Read</span></strong></h2>
<h3><a href="https://corporatefinanceinstitute.com/resources/accounting/tax-depreciation/" target="_blank" rel="noopener">Tax Depreciation</a></h3>
<p><strong>Citation:</strong> CFI Team. (2022, December 28). <em>Tax Depreciation</em>. Retrieved from CFI: https://corporatefinanceinstitute.com/resources/accounting/tax-depreciation/</p>
<hr style="border-top: 8px solid #AC1A2F;">
```

## Chapter 10

Suggested use:

- Paste this into the Chapter 10 Learning Activities page.
- The page likely already has its own title, so this block starts with content sections rather than a second page title.
- The template icon URLs used in this block were verified against the unpublished template pages already present in course `16866`.
- If you prefer visual copy/paste over HTML, the unpublished `Template: Image Customizations` page in `16866` can be used to copy the icon + heading pairs directly.
- Omit any empty template sections. If a module does not actually have additional resources, do not keep an `Additional Resources` header just for template consistency.

```html
<hr style="border-top: 10px solid #AC1A2F; border-bottom: none; margin: 0 0 16px 0;" />
<h2><img role="presentation" src="https://sinclair.instructure.com/courses/16866/files/498556/preview?verifier=TdMt16m17rIJ4MEpwx8CF2FSvXgr9cQiG7XQzKGW" alt="" width="45" data-decorative="true" loading="lazy"><span style="color: #ac1a2f;"><strong>View</strong></span></h2>
<h3><a href="https://turbotax.intuit.com/tax-tips/tax-deductions-and-credits/video-what-are-tax-deductions/L9ecr89qQ" target="_blank" rel="noopener">What Are Tax Deductions?</a></h3>
<p><strong>Citation:</strong> TurboTax Expert. (2022, December 01). <em>Video: What Are Tax Deductions?</em> Retrieved from TurboTax: https://turbotax.intuit.com/tax-tips/tax-deductions-and-credits/video-what-are-tax-deductions/L9ecr89qQ</p>
<h3><a href="https://www.youtube.com/watch?v=ZcDTEYW08mI" target="_blank" rel="noopener">Credit Limit Worksheet A Walkthrough (Schedule 8812)</a></h3>
<p><strong>Citation:</strong> Teach Me Personal Finance. (2023, August 8). <em>Credit Limit Worksheet A Walkthrough (Schedule 8812)</em>. Retrieved from YouTube: https://www.youtube.com/watch?v=ZcDTEYW08mI</p>
<hr>
<h2><strong><span style="color: #ac1a2f;"><img role="presentation" src="https://sinclair.instructure.com/courses/16866/files/498526/preview?verifier=1M8QHy00HYy8iuXE9FstTpMQ6EWOZ3nu70xYyaOs" alt="" width="45" data-decorative="true" loading="lazy">Read</span></strong></h2>
<h3><a href="https://www.investopedia.com/articles/taxes/08/itemized-deductions-overview.asp" target="_blank" rel="noopener">Itemized Deductions: What It Means and How to Claim</a></h3>
<p><strong>Citation:</strong> Cussen, M. P. (2023, March 20). <em>Itemized Deductions: What It Means and How to Claim</em>. Retrieved from Investopedia: https://www.investopedia.com/articles/taxes/08/itemized-deductions-overview.asp</p>
<hr style="border-top: 8px solid #AC1A2F;">
```

## Chapter 5

Suggested use:

- Paste this into the Chapter 5 Learning Activities page.
- The article title has been normalized to match the actual linked Investopedia page.
- The video citation is reconstructed from the linked video title because the D2L export did not preserve a citation for it.

```html
<hr style="border-top: 10px solid #AC1A2F; border-bottom: none; margin: 0 0 16px 0;" />
<h2><strong><span style="color: #ac1a2f;"><img role="presentation" src="https://sinclair.instructure.com/courses/16866/files/498526/preview?verifier=1M8QHy00HYy8iuXE9FstTpMQ6EWOZ3nu70xYyaOs" alt="" width="45" data-decorative="true" loading="lazy">Read</span></strong></h2>
<h3><a href="https://www.investopedia.com/terms/i/identified-shares.asp" target="_blank" rel="noopener">Income Exclusion Rule Explained: Understanding Tax-Free Income</a></h3>
<p><strong>Citation:</strong> Kagan, J. (2026, January 16). <em>Income Exclusion Rule Explained: Understanding Tax-Free Income</em>. Retrieved from Investopedia: https://www.investopedia.com/terms/i/identified-shares.asp</p>
<hr>
<h2><img role="presentation" src="https://sinclair.instructure.com/courses/16866/files/498556/preview?verifier=TdMt16m17rIJ4MEpwx8CF2FSvXgr9cQiG7XQzKGW" alt="" width="45" data-decorative="true" loading="lazy"><span style="color: #ac1a2f;"><strong>View</strong></span></h2>
<h3><a href="https://www.youtube.com/watch?v=yGQ79M7nIyI" target="_blank" rel="noopener">What are Tax Write-Offs? Tax Deductions Explained by a CPA!</a></h3>
<p><strong>Citation:</strong> YouTube. (n.d.). <em>What are Tax Write-Offs? Tax Deductions Explained by a CPA!</em>. Retrieved from YouTube: https://www.youtube.com/watch?v=yGQ79M7nIyI</p>
<hr style="border-top: 8px solid #AC1A2F;">
```

## Chapter 11

- The D2L resource target and the current Canvas module item both use the same YouTube URL.
- The mismatch appears to be in the saved citation text, so the citation below is normalized to the actual linked video URL.

```html
<hr style="border-top: 10px solid #AC1A2F; border-bottom: none; margin: 0 0 16px 0;" />
<h2><img role="presentation" src="https://sinclair.instructure.com/courses/16866/files/498556/preview?verifier=TdMt16m17rIJ4MEpwx8CF2FSvXgr9cQiG7XQzKGW" alt="" width="45" data-decorative="true" loading="lazy"><span style="color: #ac1a2f;"><strong>View</strong></span></h2>
<h3><a href="https://www.youtube.com/watch?v=2NTnhGeVNZ4" target="_blank" rel="noopener">Interaction of Passive Activity Loss &amp; At Risk Limits ¦ Income Tax Course ¦ CPA exam Regulation</a></h3>
<p><strong>Citation:</strong> Farhat Lectures. (n.d.). <em>Passive Activity Loss &amp; At Risk Limits ¦ Income Tax Course ¦ CPA exam Regulation ¦ Tax Cuts and Jobs</em>. Retrieved from YouTube: https://www.youtube.com/watch?v=2NTnhGeVNZ4</p>
<hr style="border-top: 8px solid #AC1A2F;">
```

## Completed Chapters
- Chapter 1
- Chapter 2
- Chapter 3
- Chapter 4
- Chapter 5
- Chapter 8
- Chapter 10
- Chapter 11
