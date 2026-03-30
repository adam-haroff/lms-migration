# ACC 2321 Link Citation Map

Updated March 27, 2026.

This file is for ACC 2321 cleanup in Canvas. It is based on the D2L export at [d2l-export.zip](/Users/adam.haroff/Desktop/projects/codex/lms-migration/resources/incoming/acc-2321/before/d2l-export.zip), especially `imsmanifest.xml`, where D2L stored many link descriptions and citations.

## Recommended Pattern

- If a resource needs a citation or explanation, place it on a Canvas page, not as a bare module External URL item.
- Use descriptive link text.
- Open external links in a new tab with `target="_blank"` and `rel="noopener"`.
- Once a module's citations are moved inline onto the page, you can remove the blanket note that says citations are at the end of the Overview section.

## Confidence Key

- `Recovered cleanly`: title, URL, and citation agree well enough to reuse directly.
- `Use with caution`: D2L metadata conflicts or has obvious errors; verify before publishing.
- `Manual only`: no citation was preserved in the D2L description area.

## Suggested HTML Pattern

Use this pattern for most cited external resources:

```html
<p><a href="https://example.com" target="_blank" rel="noopener">Resource Title</a></p>
<p><strong>Citation:</strong> Source citation here.</p>
```

If the D2L item also had explanatory text, use:

```html
<p><a href="https://example.com" target="_blank" rel="noopener">Resource Title</a></p>
<p>Short description or instructional note.</p>
<p><strong>Citation:</strong> Source citation here.</p>
```

## Module-Level Resources

### Chapter 1

Status: `Recovered cleanly`

**Taxes: Crash Course Economics #31**

- URL: `https://www.youtube.com/watch?v=7Qtr_vA3Prw`
- Citation: `Crash Course. (2016, April 27). All About Taxes. Retrieved from YouTube: https://www.youtube.com/watch?v=7Qtr_vA3Prw`

```html
<p><a href="https://www.youtube.com/watch?v=7Qtr_vA3Prw" target="_blank" rel="noopener">Taxes: Crash Course Economics #31</a></p>
<p><strong>Citation:</strong> Crash Course. (2016, April 27). <em>All About Taxes</em>. Retrieved from YouTube: https://www.youtube.com/watch?v=7Qtr_vA3Prw</p>
```

Status: `Recovered cleanly`

**Reducing tax rates by reducing tax bias**

- URL: `https://www.brookings.edu/articles/reducing-tax-rates-by-reducing-tax-bias/`
- Citation: `Pozen, R. (2015, June 23). Reducing Tax Rates by Reducing Tax Bias. Retrieved from Brookings: https://www.brookings.edu/articles/reducing-tax-rates-by-reducing-tax-bias/`

```html
<p><a href="https://www.brookings.edu/articles/reducing-tax-rates-by-reducing-tax-bias/" target="_blank" rel="noopener">Reducing tax rates by reducing tax bias</a></p>
<p><strong>Citation:</strong> Pozen, R. (2015, June 23). <em>Reducing Tax Rates by Reducing Tax Bias</em>. Retrieved from Brookings: https://www.brookings.edu/articles/reducing-tax-rates-by-reducing-tax-bias/</p>
```

### Chapter 2

Status: `Recovered cleanly`

**Gender differences in taxation: Why do they matter?**

- URL: `https://blogs.worldbank.org/developmenttalk/gender-differences-taxation-why-do-they-matter`
- Citation: `Grown, C., Ozer, C., & Bronchi, C. (2022, July 20). Gender Difference in Taxation: Why Do They Matter? Retrieved from World Bank Blogs: https://blogs.worldbank.org/developmenttalk/gender-differences-taxation-why-do-they-matter`

```html
<p><a href="https://blogs.worldbank.org/developmenttalk/gender-differences-taxation-why-do-they-matter" target="_blank" rel="noopener">Gender differences in taxation: Why do they matter?</a></p>
<p><strong>Citation:</strong> Grown, C., Ozer, C., &amp; Bronchi, C. (2022, July 20). <em>Gender Difference in Taxation: Why Do They Matter?</em> Retrieved from World Bank Blogs: https://blogs.worldbank.org/developmenttalk/gender-differences-taxation-why-do-they-matter</p>
```

### Chapter 3

Status: `Recovered cleanly`

**How inequities in U.S. taxation can perpetuate systemic racism**

- URL: `https://equitablegrowth.org/how-inequities-in-u-s-taxation-can-perpetuate-systemic-racism/`
- Citation: `Harrison, S. (2021, April 20). Washington Center for Equitable Growth. Retrieved from Equitable Growth: https://equitablegrowth.org/how-inequities-in-u-s-taxation-can-perpetuate-systemic-racism/`

```html
<p><a href="https://equitablegrowth.org/how-inequities-in-u-s-taxation-can-perpetuate-systemic-racism/" target="_blank" rel="noopener">How inequities in U.S. taxation can perpetuate systemic racism</a></p>
<p><strong>Citation:</strong> Harrison, S. (2021, April 20). <em>Washington Center for Equitable Growth</em>. Retrieved from Equitable Growth: https://equitablegrowth.org/how-inequities-in-u-s-taxation-can-perpetuate-systemic-racism/</p>
```

**Kiddie Tax Discussion**

- URL: `https://www.investopedia.com/terms/k/kiddietax.asp`
- Citation: `Kagan, J. (2025, September 10). Understanding Kiddie Tax: Rules and Rates. Retrieved from Investopedia: https://www.investopedia.com/terms/k/kiddietax.asp`

```html
<p><a href="https://www.investopedia.com/terms/k/kiddietax.asp" target="_blank" rel="noopener">Kiddie Tax Discussion</a></p>
<p><strong>Citation:</strong> Kagan, J. (2025, September 10). <em>Understanding Kiddie Tax: Rules and Rates</em>. Retrieved from Investopedia: https://www.investopedia.com/terms/k/kiddietax.asp</p>
```

### Chapter 4

Status: `Recovered cleanly`

**What Is a Progressive Tax? Advantages and Disadvantages**

- URL: `https://www.investopedia.com/terms/p/progressivetax.asp`
- Citation: `Kagan, J. (2023, March 04). What is a Progressive Tax? Advantages and Disadvantages. Retrieved from Investopedia: https://www.investopedia.com/terms/p/progressivetax.asp`

```html
<p><a href="https://www.investopedia.com/terms/p/progressivetax.asp" target="_blank" rel="noopener">What Is a Progressive Tax? Advantages and Disadvantages</a></p>
<p><strong>Citation:</strong> Kagan, J. (2023, March 04). <em>What Is a Progressive Tax? Advantages and Disadvantages</em>. Retrieved from Investopedia: https://www.investopedia.com/terms/p/progressivetax.asp</p>
```

### Chapter 5

Status: `Recovered with one reconstructed video citation`

**Income Exclusion Rule Explained: Understanding Tax-Free Income**

- URL: `https://www.investopedia.com/terms/i/identified-shares.asp`
- Notes:
  - The URL slug is misleading, but the live Investopedia page title matches the income exclusion topic.
  - The link text has been normalized to the actual page title.
- Citation: `Kagan, J. (2026, January 16). Income Exclusion Rule Explained: Understanding Tax-Free Income. Retrieved from Investopedia: https://www.investopedia.com/terms/i/identified-shares.asp`

```html
<p><a href="https://www.investopedia.com/terms/i/identified-shares.asp" target="_blank" rel="noopener">Income Exclusion Rule Explained: Understanding Tax-Free Income</a></p>
<p><strong>Citation:</strong> Kagan, J. (2026, January 16). <em>Income Exclusion Rule Explained: Understanding Tax-Free Income</em>. Retrieved from Investopedia: https://www.investopedia.com/terms/i/identified-shares.asp</p>
```

**What are Tax Write-Offs? Tax Deductions Explained by a CPA!**

- URL: `https://www.youtube.com/watch?v=yGQ79M7nIyI`
- Citation: `Reconstructed from the linked video title: YouTube. (n.d.). What are Tax Write-Offs? Tax Deductions Explained by a CPA! Retrieved from YouTube: https://www.youtube.com/watch?v=yGQ79M7nIyI`

```html
<p><a href="https://www.youtube.com/watch?v=yGQ79M7nIyI" target="_blank" rel="noopener">What are Tax Write-Offs? Tax Deductions Explained by a CPA!</a></p>
<p><strong>Citation:</strong> YouTube. (n.d.). <em>What are Tax Write-Offs? Tax Deductions Explained by a CPA!</em>. Retrieved from YouTube: https://www.youtube.com/watch?v=yGQ79M7nIyI</p>
```

### Chapter 8

Status: `Recovered cleanly`

**Tax Depreciation**

- URL: `https://corporatefinanceinstitute.com/resources/accounting/tax-depreciation/`
- Citation: `CFI Team. (2022, December 28). Tax Depreciation. Retrieved from CFI: https://corporatefinanceinstitute.com/resources/accounting/tax-depreciation/`

```html
<p><a href="https://corporatefinanceinstitute.com/resources/accounting/tax-depreciation/" target="_blank" rel="noopener">Tax Depreciation</a></p>
<p><strong>Citation:</strong> CFI Team. (2022, December 28). <em>Tax Depreciation</em>. Retrieved from CFI: https://corporatefinanceinstitute.com/resources/accounting/tax-depreciation/</p>
```

### Chapter 10

Status: `Recovered cleanly`

**What Are Tax Deductions?**

- URL: `https://turbotax.intuit.com/tax-tips/tax-deductions-and-credits/video-what-are-tax-deductions/L9ecr89qQ`
- Citation: `TurboTax Expert. (2022, December 01). Video: What Are Tax Deductions? Retrieved from TurboTax: https://turbotax.intuit.com/tax-tips/tax-deductions-and-credits/video-what-are-tax-deductions/L9ecr89qQ`

```html
<p><a href="https://turbotax.intuit.com/tax-tips/tax-deductions-and-credits/video-what-are-tax-deductions/L9ecr89qQ" target="_blank" rel="noopener">What Are Tax Deductions?</a></p>
<p><strong>Citation:</strong> TurboTax Expert. (2022, December 01). <em>Video: What Are Tax Deductions?</em> Retrieved from TurboTax: https://turbotax.intuit.com/tax-tips/tax-deductions-and-credits/video-what-are-tax-deductions/L9ecr89qQ</p>
```

Status: `Recovered cleanly`

**Itemized Deductions: What It Means and How to Claim**

- URL: `https://www.investopedia.com/articles/taxes/08/itemized-deductions-overview.asp`
- Citation: `Cussen, M. P. (2023, March 20). Itemized Deductions: What It Means and How to Claim. Retrieved from Investopedia: https://www.investopedia.com/articles/taxes/08/itemized-deductions-overview.asp`

```html
<p><a href="https://www.investopedia.com/articles/taxes/08/itemized-deductions-overview.asp" target="_blank" rel="noopener">Itemized Deductions: What It Means and How to Claim</a></p>
<p><strong>Citation:</strong> Cussen, M. P. (2023, March 20). <em>Itemized Deductions: What It Means and How to Claim</em>. Retrieved from Investopedia: https://www.investopedia.com/articles/taxes/08/itemized-deductions-overview.asp</p>
```

Status: `Use with caution`

**Credit Limit Worksheet A Walkthrough (Schedule 8812)**

- The clean imported URL is `https://www.youtube.com/watch?v=ZcDTEYW08mI`.
- The D2L citation text contains a malformed URL string.
- The stable local evidence uses `Schedule 8812`, including the D2L manual-review trail and the `16866` Canvas snapshot.

```html
<p><a href="https://www.youtube.com/watch?v=ZcDTEYW08mI" target="_blank" rel="noopener">Credit Limit Worksheet A Walkthrough (Schedule 8812)</a></p>
<p><strong>Citation:</strong> Teach Me Personal Finance. (2023, August 8). <em>Credit Limit Worksheet A Walkthrough (Schedule 8812)</em>. Retrieved from YouTube: https://www.youtube.com/watch?v=ZcDTEYW08mI</p>
```

### Chapter 11

Status: `Recovered with normalized citation URL`

**Interaction of Passive Activity Loss & At Risk Limits ¦ Income Tax Course ¦ CPA exam Regulation**

- The D2L resource href and the imported Canvas URL both point to `https://www.youtube.com/watch?v=2NTnhGeVNZ4`.
- The mismatch is inside the saved D2L citation text, which points to a different YouTube URL.
- Recommendation: use the actual linked video URL and normalize the citation to match it.

Recommended HTML:

```html
<p><a href="https://www.youtube.com/watch?v=2NTnhGeVNZ4" target="_blank" rel="noopener">Interaction of Passive Activity Loss &amp; At Risk Limits ¦ Income Tax Course ¦ CPA exam Regulation</a></p>
<p><strong>Citation:</strong> Farhat Lectures. (n.d.). <em>Passive Activity Loss &amp; At Risk Limits ¦ Income Tax Course ¦ CPA exam Regulation ¦ Tax Cuts and Jobs</em>. Retrieved from YouTube: https://www.youtube.com/watch?v=2NTnhGeVNZ4</p>
```

## Course-Level Resource Links

These are not chapter Learning Activities links, but they also had useful D2L description data.

### Tax Resources & Other Accounting Resources

Status: `Recovered cleanly`

**IRS (Internal Revenue Service) Website**

- Recommended URL to use: `https://www.irs.gov/`
- D2L had both `http://irs.gov` and `https://www.irs.gov/`; use the secure `https` version.

```html
<p><a href="https://www.irs.gov/" target="_blank" rel="noopener">IRS (Internal Revenue Service) Website</a></p>
<p>This is where students can find forms, form instructions, IRS publications, and other tax-reference materials used throughout the course.</p>
<p><strong>Citation:</strong> IRS | Internal Revenue Service. (2023, July 12). <em>IRS.GOV</em>. Retrieved from IRS.GOV: https://www.irs.gov/</p>
```

Status: `Recovered cleanly`

**IRC (Internal Revenue Code) -- Cornell University**

```html
<p><a href="https://www.law.cornell.edu/uscode/text/26" target="_blank" rel="noopener">IRC (Internal Revenue Code) -- Cornell University</a></p>
<p>This is a free IRC database maintained by Cornell University.</p>
<p><strong>Citation:</strong> Cornell Law School. (2023, July 12). <em>LLI | Legal Information Institute</em>. Retrieved from US Code Title 26: https://www.law.cornell.edu/uscode/text/26</p>
```

Status: `Recovered cleanly`

**Revenue (Federal) Regulations**

```html
<p><a href="https://www.govinfo.gov/app/collection/cfr/2019/title26/chapterI" target="_blank" rel="noopener">Revenue (Federal) Regulations</a></p>
<p>Open the site, open the most current year, and then go to Title 26 to locate specific federal tax regulations.</p>
<p><strong>Citation:</strong> GovInfo. (2023, July 12). <em>Code of Federal Regulations (Annual Edition)</em>. Retrieved from GovInfo: https://www.govinfo.gov/app/collection/cfr/2019/title26/chapterI</p>
```

Status: `Manual only`

**Plante Moran Free Education**

- D2L preserved the item title, but not a description or citation in `imsmanifest.xml`.
- If you want to keep this link with a citation, you will need to copy the context manually from the D2L page or confirm the exact intended source in the live course.

## No Recoverable Module-Link Citations Found

I did not find citation-bearing module-link descriptions in `imsmanifest.xml` for these chapters:

- Chapter 6
- Chapter 7
- Chapter 9
- Chapter 12
- Chapter 13
- Chapter 14
- Chapter 16

That does not prove those modules had no source links. It means the D2L manifest did not preserve citation text for module items in a way I could recover directly. For those, you will likely need to copy the citation manually from the D2L course content or from the linked source itself.
