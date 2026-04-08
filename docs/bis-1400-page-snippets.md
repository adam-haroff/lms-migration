# BIS 1400 Page Cleanup Snippets

Use this file while editing the live Canvas course `17038`.

Guiding rules:

- For `Introduction and Checklist` pages, keep the template page structure from `Module: Introduction and Checklist Template`.
- Replace only the checklist `<ol>` when possible instead of rebuilding the whole page.
- Remove links from checklist items. If something should remain available to students, add it as a real module item instead of linking to it from the checklist.
- For `Learning Activities` pages, use the title and section header structure from `Module T1: Learning Activities`.
- For external links, let Canvas normalize the final markup after save.

## First Work Block

Do these in order:

1. Update `What is a Mystery Shop?`
2. Replace the checklist list on `Module 1: Introduction and Checklist`
3. Move `Instructions: Mystery Shopper Report` into the Canvas assignment description
4. In Modules 5, 6, and 7:
   - replace the checklist list
   - then clean the matching `Learning Activities` page

For the checklist pages, do not rebuild the whole page unless you have to. The cleanest edit is:

1. Open the page in Canvas.
2. Edit the page.
3. Switch to the HTML editor.
4. Replace only the `<ol>...</ol>` under `Module Checklist`.
5. Leave the `Introduction`, `Module Objectives`, and `Select Next to Get Started` pieces in place.

## What Is a Mystery Shop?

Recommended full body replacement:

Keep this page source-faithful. Do not restructure the ending into a template-style checklist.

```html
<hr style="border-top: 10px solid #AC1A2F; border-bottom: none; margin: 0 0 16px 0;" />
<p>Most businesses provide at least &ldquo;basic&rdquo; customer service &ndash; if you go into a retail store and walk to the cash register with an item, the associate will ring up your order and make the sale. During your mystery shop, you want to see how far beyond the &ldquo;basics&rdquo; the associate you ask is willing to go to assist you.</p>
<div style="display: flex; align-items: flex-start; gap: 12px; flex-wrap: wrap; margin-bottom: 12px;">
    <img src="https://sinclair.instructure.com/courses/17038/files/516824/preview" alt="" width="180" height="180" style="max-width: 100%; height: auto; flex: 0 0 auto;" data-api-endpoint="https://sinclair.instructure.com/api/v1/courses/17038/files/516824" data-api-returntype="File" />
    <div style="flex: 1 1 320px; min-width: 240px;">
        <p style="margin-top: 0;">Students will act as a "Mystery Shopper" by going to a business or organization, and evaluating the service received.</p>
        <p>Watch this short 9 minute video to see an example of mystery shopping: <a class="inline_disabled" href="https://youtu.be/N1cIdXoLw_g" target="_blank" rel="noopener">https://youtu.be/N1cIdXoLw_g</a>.</p>
    </div>
</div>
<p>You are NOT limited to the examples below, but here are some ideas you could consider:</p>
<ul>
    <li>Shop a retail store and tell the associate you are going on vacation and need help finding a few items. Is the associate willing to walk the store with you to find the items that you need, or does the associate point to the sunscreen aisle and leave you to fend for yourself? If you don&rsquo;t have a situation that requires you to shop for a number of items located in at least a couple of different departments, your scenario can be to shop a retail store and ask for assistance from 4-5 different associates during your visit so that you get a feel for the overall customer service culture of the organization.</li>
    <li>Go to a restaurant and make several &ldquo;special&rdquo; requests: to sit away from an air conditioning vent, to sit in a booth instead of a table, ask for substitutions in menu items - can I have mashed potatoes with my entr&eacute;e instead of the rice that comes with it? Or give specific instructions for how your meal should be prepared and see if the directions are followed to specifications.</li>
    <li>Go to a department at Sinclair &ndash; maybe Advising where you would ask for assistance deciding between several degree majors, or Financial Aid office where you would ask for financial aid options beyond federal and state grants and loans, or the Co-op and Internship office where you ask about specific information about opportunities within your major course of study.</li>
    <li>Go to your bank and ask about credit card or loan options specific to your needs - low interest, low monthly payments, benefits like no monthly service charge on your checking account if you have a mortgage loan, etc. Make sure you know what&rsquo;s important in a loan for you, and ask for an explanation of how much you will ultimately pay with the different loan options.</li>
</ul>
<p>It is great if you choose a situation for which you really would like assistance. You can get useful information that you need anyway, and you&rsquo;ll be in a good position to determine if the organization, and the particular associate you worked with, provide quality customer service.</p>
<p>This project consists of three "assignments" - specific information about these assignments can be found on the individual pages:</p>
<ol>
    <li><strong>Before: Mystery Shop Discussion</strong>. You will provide your plan for your mystery shop: business selected, background information on business, and your planned scenario.</li>
    <li><strong>During: Mystery Shop Evaluation Form</strong>, used to plan and evaluate your experience (see the Mystery Shop Evaluation Form).</li>
    <li><strong>Mystery Shop Report</strong>: A final report of your mystery shop experience (see the Mystery Shop Paper assignment).</li>
</ol>
<hr style="border-top: 8px solid #AC1A2F; clear: left;">
```

Notes:

- If you want to add a citation for the video, verify the title, creator, and date first. Do not guess.
- The important cleanup items here are:
  - fix the `S tudents` typo
  - normalize the video link
  - increase image margin
  - replace `eLearn dropbox` wording with Canvas-equivalent wording only

## Checklist Replacement Lists

These are recommended replacements for the ordered list under `Module Checklist`.

Important:

- Module 1 already has the PowerPoint as a real module item.
- The Chapter 2-10 PowerPoint files already exist in the live course files, even though most are not currently placed in modules.
- Recommended approach: add each PowerPoint to its module as a file item and include `(optional)` in the module item title for clarity.
- Once the optional PowerPoints are added to the modules, keep them in the checklist wording too for consistency with the original D2L flow.
- Suggested module-item pattern: `Chapter X PowerPoint Presentation (optional)`
- Use `Read: Lesson: ...` for substantive Canvas content pages.
- Use `Review: ...` for optional PowerPoints and lighter support/reference materials.

### Module 1

```html
<ol>
  <li><strong>Read and take notes</strong>: Textbook Chapter 1, <em>The World of Customer Service</em></li>
  <li><strong>Review</strong>: PowerPoint Presentation: Chapter 1</li>
  <li><strong>Participate in</strong>: Discussion: Career Progression</li>
  <li><strong>Complete</strong>: Quiz: Chapter 1</li>
</ol>
```

### Module 2

```html
<ol>
  <li><strong>Read and take notes</strong>: Textbook Chapter 2, <em>Contributing to the Service Culture</em></li>
  <li><strong>Review</strong>: PowerPoint Presentation: Chapter 2 (optional)</li>
  <li><strong>Participate in</strong>: Discussion: What Makes The Perfect Customer Service Rep?</li>
  <li><strong>Complete</strong>: Quiz: Chapter 2</li>
</ol>
```

### Module 3

```html
<ol>
  <li><strong>Read and take notes</strong>: Textbook Chapter 3, <em>Verbal Communication Skills</em></li>
  <li><strong>Review</strong>: PowerPoint Presentation: Chapter 3 (optional)</li>
  <li><strong>Read</strong>: Lesson: When Your Best is Not Enough!</li>
  <li><strong>Participate in</strong>: Discussion: Communications Breakdown in Customer Service</li>
  <li><strong>Complete</strong>: Quiz: Chapter 3</li>
</ol>
```

### Module 4

```html
<ol>
  <li><strong>Read and take notes</strong>: Textbook Chapter 4, <em>Nonverbal Communication</em></li>
  <li><strong>Review</strong>: PowerPoint Presentation: Chapter 4 (optional)</li>
  <li><strong>Participate in</strong>: Discussion: Distracting Nonverbal Cues</li>
  <li><strong>Complete</strong>: Quiz: Chapter 4</li>
</ol>
```

### Module 5

```html
<ol>
  <li><strong>Read and take notes</strong>: Textbook Chapter 5, <em>Listening to the Customer</em></li>
  <li><strong>Review</strong>: PowerPoint Presentation: Chapter 5 (optional)</li>
  <li><strong>Complete</strong>: Learning Activities</li>
  <li><strong>Participate in</strong>: Discussion: Five Ways to Listen Better</li>
  <li><strong>Complete</strong>: Quiz: Chapter 5</li>
</ol>
```

### Module 6

```html
<ol>
  <li><strong>Read and take notes</strong>: Textbook Chapter 6, <em>Customer Service and Behavior</em></li>
  <li><strong>Review</strong>: PowerPoint Presentation: Chapter 6 (optional)</li>
  <li><strong>Read</strong>: Lesson: Customer Service and Behavior</li>
  <li><strong>Complete</strong>: Learning Activities</li>
  <li><strong>Participate in</strong>: Discussion: Ethical Dilemma 6.1</li>
  <li><strong>Complete</strong>: Quiz: Chapter 6</li>
</ol>
```

### Module 7

```html
<ol>
  <li><strong>Read and take notes</strong>: Textbook Chapter 7, <em>Service Breakdowns and Recovery</em></li>
  <li><strong>Review</strong>: PowerPoint Presentation: Chapter 7 (optional)</li>
  <li><strong>Complete</strong>: Learning Activities</li>
  <li><strong>Read</strong>: Lesson: Difficult Customers</li>
  <li><strong>Read</strong>: Lesson: Service Recovery</li>
  <li><strong>Participate in</strong>: Discussion: Dealing with Difficult Customers</li>
  <li><strong>Complete</strong>: Quiz: Chapter 7</li>
</ol>
```

### Module 8

```html
<ol>
  <li><strong>Read and take notes</strong>: Textbook Chapter 8, <em>Customer Service in a Diverse World</em></li>
  <li><strong>Review</strong>: PowerPoint Presentation: Chapter 8 (optional)</li>
  <li><strong>Participate in</strong>: Discussion: Diversity</li>
  <li><strong>Complete</strong>: Quiz: Chapter 8</li>
</ol>
```

### Module 9

```html
<ol>
  <li><strong>Read and take notes</strong>: Textbook Chapter 9, <em>Customer Service via Technology</em></li>
  <li><strong>Review</strong>: PowerPoint Presentation: Chapter 9 (optional)</li>
  <li><strong>Complete</strong>: Assignment: Identifying Power Skills</li>
  <li><strong>Complete</strong>: Quiz: Chapter 9</li>
</ol>
```

### Module 10

```html
<ol>
  <li><strong>Read and take notes</strong>: Textbook Chapter 10, <em>Encouraging Customer Loyalty</em></li>
  <li><strong>Review</strong>: PowerPoint Presentation: Chapter 10 (optional)</li>
  <li><strong>Read</strong>: Lesson: Building Stronger Relationships</li>
  <li><strong>Participate in</strong>: Discussion: Customer Loyalty</li>
  <li><strong>Complete</strong>: Quiz: Chapter 10</li>
</ol>
```

### Module 13

If you want consistency with the other modules, rename the page `Introduction and Objectives` to `Module 13: Introduction and Checklist` before updating the checklist.

```html
<ol>
  <li><strong>Complete</strong>: Final Exam</li>
  <li><strong>Complete</strong>: End of Course Survey</li>
</ol>
```

## Learning Activities Pages

Use the title and section headers from `Module T1: Learning Activities`. The content blocks below are intended to replace the body content beneath those headers.

### Module 5 Learning Activities

Keep this page, but restructure it to match the template.

Suggested content:

```html
<hr style="border-top: 10px solid #AC1A2F; border-bottom: none; margin: 0 0 16px 0;">
<h2><strong><span style="color: #ac1a2f;"><img role="presentation" src="https://sinclair.instructure.com/courses/17038/files/516635/preview" alt="" width="45" data-api-endpoint="https://sinclair.instructure.com/api/v1/courses/17038/files/516635" data-api-returntype="File">Read</span></strong></h2>
<h3><a class="inline_disabled" href="http://www.communicator-communication.blogspot.com/" target="_blank" rel="noopener">10 Listening Tips</a></h3>
<p>Listening is an important skill and should not be taken for granted. Whether it is for taking direction from the boss, or understanding peers during a conference call. Listening is the first step towards helping the customer. Please use the following link to get some very useful listening tips.</p>
<h3><a class="inline_disabled" href="http://www.bizjournals.com/seattle/stories/2003/08/11/smallb2.html?page=all" target="_blank" rel="noopener">Hone Your Active Listening Skills</a></h3>
<p>People can often be preoccupied with work they consider more important than the customer in front of them. There may be inventory that has to be stocked before quitting time. The telephone may start ringing or you already have someone on hold. It could be that an employee called off work and you're short staffed. You've got a lot going on. You're tired and it's close to quitting time, or you're hungry and it's past your lunch hour. There could be any number of distractions you feel are more deserving of your attention than that customer standing there in front of them, but you couldn't be more wrong.</p>
<p>We discussed body language in a previous chapter, so you know that people can pick up on whether or not you are engaged. Please use the following link for ways to hone your active listening skills.</p>
<hr>
<h2><strong><span style="color: #ac1a2f;"><img role="presentation" src="https://sinclair.instructure.com/courses/17038/files/516656/preview" alt="" width="45" data-api-endpoint="https://sinclair.instructure.com/api/v1/courses/17038/files/516656" data-api-returntype="File">Try This</span></strong></h2>
<p>Listening skills are a key component to effective communication. Are you a good listener? Please use the following link to take a quick quiz to find out.</p>
<ul>
  <li><a class="inline_disabled" href="http://www.funquizcards.com/quiz/personality/are-you-a-good-listener.php" target="_blank" rel="noopener">Are You A Good Listener?</a></li>
</ul>
<hr style="border-top: 8px solid #AC1A2F; clear: left;">
```

### Module 6 Learning Activities

Keep this page, but reorganize it into clearer sections.

Suggested content:

```html
<hr style="border-top: 10px solid #AC1A2F; border-bottom: none; margin: 0 0 16px 0;">
<h2><img role="presentation" src="https://sinclair.instructure.com/courses/17038/files/516656/preview" alt="" width="45" height="45" data-api-endpoint="https://sinclair.instructure.com/api/v1/courses/17038/files/516656" data-api-returntype="File"><span style="color: #ac1a2f;"><strong>Do This</strong></span></h2>
<h3><a class="inline_disabled" href="http://www.humanmetrics.com/cgi-win/JTypes1.htm" target="_blank" rel="noopener">Jung Typology Test</a></h3>
<p>Click on the link below to access the HumanMetrics web site. Take the Jung Typology Test.</p>
<p>Read the directions carefully, then click the &quot;Do It&quot; button at the bottom of the screen. Answer the 72 questions and click the &quot;Submit It&quot; button when you are finished with the test. Don&rsquo;t spend too much time contemplating each question. Select the answer that seems to best describe your preference.</p>
<p>Once your survey is scored, note your four (4) letter behavioral profile, for example ENTJ, and also note the number associated with each letter. Then read the type indicator summary, or summaries, authored by Keirsey, Butt, Heiss, etc.</p>
<hr>
<h2><img role="presentation" src="https://sinclair.instructure.com/courses/17038/files/516649/preview" alt="" width="45" height="45" data-api-endpoint="https://sinclair.instructure.com/api/v1/courses/17038/files/516649" data-api-returntype="File"><span style="color: #ac1a2f;"><strong>Explore This</strong></span></h2>
<p>Check out the following links related to positive words and phrasing.</p>
<ul>
  <li><a class="inline_disabled" href="http://www.winspiration.co.uk/positive.htm" target="_blank" rel="noopener">Winspriration</a></li>
  <li><a class="inline_disabled" href="http://www.creativeaffirmations.com/positive-words.html" target="_blank" rel="noopener">Redefining Positive for Real Life</a></li>
  <li><a class="inline_disabled" href="http://www.virtuescience.com/treasure.html" target="_blank" rel="noopener">Virtue Science</a></li>
  <li><a class="inline_disabled" href="http://www.evancarmichael.com/Sales/455/How-Positive-words-can-change-the-mind-of-the-customer.html" target="_blank" rel="noopener">Evan Carmichael</a></li>
  <li><a class="inline_disabled" href="http://customersrock.net/2007/01/17/yes-the-words-we-say-do-affect-customers/" target="_blank" rel="noopener">Customers ROCK!</a></li>
  <li><a class="inline_disabled" href="http://www.work911.com/conflict/carticles/poslan.htm" target="_blank" rel="noopener">The Work911.com Supersite</a></li>
  <li><a class="inline_disabled" href="http://www.english-test.net/forum/ftopic5232.html" target="_blank" rel="noopener">English-Test</a></li>
</ul>
<hr>
<h2><img role="presentation" src="https://sinclair.instructure.com/courses/17038/files/516665/preview" alt="" width="45" data-api-endpoint="https://sinclair.instructure.com/api/v1/courses/17038/files/516665" data-api-returntype="File"><span style="color: #ac1a2f;"><strong>View</strong></span></h2>
<h3><a class="inline_disabled" href="http://highered.mcgraw-hill.com/sites/0073397113/student_view0/chapter6/videos.html#" target="_blank" rel="noopener">Communication Styles in Customer Service</a></h3>
<p>Click on the link to view the low or high resolution version of this video.</p>
<hr style="border-top: 8px solid #AC1A2F; clear: left;">
```

### Module 7 Learning Activities

Keep this page, but split the content into clearer template-style sections.

Suggested content:

```html
<hr style="border-top: 10px solid #AC1A2F; border-bottom: none; margin: 0 0 16px 0;">
<h2><strong><span style="color: #ac1a2f;"><img role="presentation" src="https://sinclair.instructure.com/courses/17038/files/516635/preview" alt="" width="45" data-api-endpoint="https://sinclair.instructure.com/api/v1/courses/17038/files/516635" data-api-returntype="File">Read</span></strong></h2>
<h3>What Is a Service Breakdown?</h3>
<p>A service breakdown can be described as any non-fulfillment or nonconformance to specified requirements. Put another way, something didn&rsquo;t do what it was supposed to do the way it was supposed to do it. When that happens, customers are typically unhappy.</p>
<p>Service breakdowns, whether real or perceived, are often the result of a systemic issue and are not always the fault of an individual. It is not limited to the way a product performs, or a service is delivered. Service breakdowns can also occur at a time when a customer calls with a question and then gets passed off from department to department, put on hold forever, or even disconnected. Breakdowns can occur when a customer service agent provides an exaggerated description of a facility which ultimately fails to meet the needs of the customer. Practically anytime you see a frustrated or angry customer, expectations have not been met, and a service breakdown is to blame.</p>
<p>Please use the following links to learn more about service breakdowns.</p>
<ul>
  <li><a class="inline_disabled" href="http://www.ehow.com/how_6575341_handle-customer-service-breakdown.html" target="_blank" rel="noopener">How to Handle a Customer Service Breakdown</a></li>
  <li><a class="inline_disabled" href="http://www.director.co.uk/ONLINE/2009/11_09_customer_care.html" target="_blank" rel="noopener">Ten Worst Customer Care Errors</a></li>
</ul>
<hr>
<h2><img role="presentation" src="https://sinclair.instructure.com/courses/17038/files/516649/preview" alt="" width="45" height="45" data-api-endpoint="https://sinclair.instructure.com/api/v1/courses/17038/files/516649" data-api-returntype="File"><span style="color: #ac1a2f;"><strong>Additional Resources</strong></span></h2>
<ul>
  <li><a class="inline_disabled" href="http://www.politickernj.com/mriccards/25131/customer-service-breakdown" target="_blank" rel="noopener">Verizon</a></li>
  <li><a class="inline_disabled" href="http://greenlagirl.com/starbucks-admits-break-down-in-customer-service/" target="_blank" rel="noopener">Starbucks</a></li>
</ul>
<hr>
<h2><img role="presentation" src="https://sinclair.instructure.com/courses/17038/files/516665/preview" alt="" width="45" data-api-endpoint="https://sinclair.instructure.com/api/v1/courses/17038/files/516665" data-api-returntype="File"><span style="color: #ac1a2f;"><strong>View</strong></span></h2>
<h3><a class="inline_disabled" href="http://www.youtube.com/watch?v=gk0eZqVpI2c" target="_blank" rel="noopener">In a Perfect World</a></h3>
<hr style="border-top: 8px solid #AC1A2F;">
```

### Module 3 Lesson: When Your Best is Not Enough!

If you need to repair the missing images on this page again later, use the following full body with the current Canvas file references from the live BIS course snapshot dated `2026-04-02`.

```html
<hr style="border-top: 10px solid #AC1A2F; border-bottom: none; margin: 0 0 16px 0;">

<p><strong>Your goal with customers is to make relationships that last.&nbsp;</strong>As we will discuss in a later module, it costs anywhere from 5 to 9 times more to gain a new customer than to keep an existing customer. Therefore, it's important for your relationships to LAST. We can use the letters-&nbsp;<strong>LAST</strong>&nbsp;-to help us remember how to listen and serve our customers.<br><strong>L&nbsp;</strong><strong>stands for Listen.&nbsp;</strong>You first need to actively listen to people in order to understand what they need. Remember the difference between active and passive listening? If not, review Module 3.</p>

<div style="display: flex; align-items: flex-start; gap: 12px; flex-wrap: wrap; margin-bottom: 12px;">
  <img src="https://sinclair.instructure.com/courses/17038/files/522403/preview" alt="smiling employee" width="144" height="216" style="max-width: 100%; height: auto; flex: 0 0 auto;" data-api-endpoint="https://sinclair.instructure.com/api/v1/courses/17038/files/522403" data-api-returntype="File">
  <div style="flex: 1 1 320px; min-width: 0;">
    <p style="margin-top: 0;"><strong>A&nbsp;</strong><strong>stands for Apologize.&nbsp;</strong>If you or your company has disappointed the customer in any way, even if you are not personally at fault, you need to apologize. You don't have to accept the blame if your company was not at fault, but you do need to apologize for any inconvenience the customer experienced. You need to adequately acknowledge and handle your customers' emotions before trying to solve his or her problem. If you skip this step, you risk creating a DISRUPTIVE customer. In fact, my experience is that most customers only become disruptive when we fail to acknowledge their emotions. Face it, everybody wants to be understood first and then they are easier to appease.</p>
    <p>What are some phrases you can use to show your guest that you empathize with them, or apologize for what they have been through? Let's try a few. "I'm sorry you had to wait so long. How may I help you?" "What a shame that shirt shrunk in the dryer. It's certainly a lovely shirt. How would you like for us to fix the problem for you?" "I'm sorry we didn't explain the return policy better to you. Since we were not clear, maybe we can work together to find a good solution for your return."</p>
    <p>Did you notice that I never made the customer feel like an idiot, even though he may not be the most logical thinker in the room? I never told the customer that it wasn't our policy and I always came across the counter (figuratively) to the customers' side in order to let him know that I was on his side and wanted to solve the problem together with him. I am not his opponent; I am his advocate. That is a huge difference. If you can grasp the fact that it is your job to HELP the customer, your words and actions will reflect your attitude and your customers will help you make them happy. Most of all, be sincere in your words and actions. People can spot a phony a mile away.</p>
  </div>
</div>

<p><strong>S&nbsp;</strong><strong>stands for Solve.</strong>&nbsp;Once you have empathized with the customer and apologized if needed, you are ready to solve his or her problem. Wait until the customer is ready to move to the solution before you push her there. Some people need to spend a good amount of time at the Apologize stage before they are ready to solve the problem. Move at the customer's pace. When you do begin to solve the problem, solve it with the customer. Ask how she would like to see the problem handled. Focus on what you CAN do for them, not on what you CAN'T do.</p>

<div style="display: flex; align-items: flex-start; gap: 12px; flex-wrap: wrap; margin-bottom: 12px;">
  <img src="https://sinclair.instructure.com/courses/17038/files/522406/preview" alt="woman thanking the customer" width="144" height="117" style="max-width: 100%; height: auto; flex: 0 0 auto;" data-api-endpoint="https://sinclair.instructure.com/api/v1/courses/17038/files/522406" data-api-returntype="File">
  <div style="flex: 1 1 320px; min-width: 240px;">
    <p style="margin-top: 0;"><strong>T&nbsp;</strong><strong>stands for Thank.&nbsp;</strong>When was the last time you were sincerely thanked for doing business someplace? Sometimes a waitress will write 'Thanks' on the bottom of the bill when I eat dinner out, but most of the time nobody ever thanks me for spending my hard-earned money at their place of business. What an impact it makes on people when you truly thank them for their business!</p>
  </div>
</div>

<p>There is one and only one company from which I will purchase women's clothing. The reason for this is because they honestly thank me for my business. On my birthday they send me a handwritten card and allow me a 10% discount on my purchase. I recently received a card from the CEO of the company and was invited to come in to get a FREE T-shirt. Did I buy anything else while I was getting my t-shirt? Of course. But, I felt really, really good and special to know that they valued my business. They say thanks for my loyalty in many, many ways throughout the year. I'll continue to do business with that company for a long time!</p>

<ol style="margin-left: 24px; padding-left: 24px;">
  <li>There are a few tips to remember when dealing with difficult customers:</li>
  <li>No matter how great your skills, there are times when your customer will become upset.</li>
  <li>Customers often become upset before they ever reach you. Customers are most often upset with the company or a policy, not with an individual. Therefore, don't take it personally.</li>
</ol>

<p>It is your job to resolve, not escalate, the problem. If you don't take the problem personally, you will be much more able to resolve the problem. Know your company's policies, but don't recite them for customers. Know when to escalate a problem and understand when you can resolve the problems on your own.</p>

<hr />

<h3>Types of Difficult Customers</h3>

<p>There are many, many types of difficult customers. Personally, I feel that anybody who is different from me can be a difficult customer. That leaves it open for anybody to be difficult! However, I can break down the types into several categories.</p>

<ul>
  <li>Angry Customers</li>
  <li>Dissatisfied Customers</li>
  <li>Indecisive Customers</li>
  <li>Demanding/Domineering Customers</li>
  <li>Rude/Inconsiderate Customers</li>
  <li>Talkative Customers</li>
  <li>Hard-to-Understand Customers</li>
</ul>

<p>Let's talk about the different ways to handle each of these types of customers, always remembering our LAST technique we learned above.</p>

<h4><strong>Angry Customers</strong></h4>
<div style="display: flex; align-items: flex-start; gap: 12px; flex-wrap: wrap; margin-bottom: 12px;">
  <img src="https://sinclair.instructure.com/courses/17038/files/522400/preview" alt="angry woman" width="144" height="217" style="max-width: 100%; height: auto; flex: 0 0 auto;" data-api-endpoint="https://sinclair.instructure.com/api/v1/courses/17038/files/522400" data-api-returntype="File">
  <div style="flex: 1 1 320px; min-width: 240px;">
    <p style="margin-top: 0;">Angry Customers require caution. These are the most at risk to turn into disruptive customers. You need to first let these customers vent totally until they are ready to be calmer. Your goal here is to move beyond the emotions to discover the reason for their anger.</p>
  </div>
</div>

<ul style="margin-left: 24px; padding-left: 24px;">
  <li>Let the customer vent. They need to know somebody is listening.</li>
  <li>Be positive. Focus on what you can do for them, not on what you can't.</li>
  <li>Know your policies, but don't recite them. A recitation of store policy will only serve to make them more upset.</li>
  <li>Acknowledge their feelings of anger.</li>
  <li>Reassure them that you will help to solve their problem</li>
  <li>Remain objective. Don't become involved with their emotions. Don't get caught up in defending your store or yourself.</li>
  <li>Determine the cause of their frustration.</li>
  <li>Negotiate a solution to their problem.</li>
  <li>Conduct a follow-up, either in person, by phone, or through the mail.</li>
</ul>

<p>If angry or dissatisfied customers can be satisfied and pleased within&nbsp;<strong>48 hours of their initial complaint</strong>, studies show that&nbsp;<strong>90% of them will continue to do business with the company</strong>. The longer it takes to satisfy the complaint, the smaller the percent of the customers will remain with the company. If the problem takes 72 hours to resolve, the retention goes down to just 71% retention.</p>

<h4><strong>Dissatisfied Customers</strong></h4>
<div style="display: flex; align-items: flex-start; gap: 12px; flex-wrap: wrap; margin-bottom: 12px;">
  <img src="https://sinclair.instructure.com/courses/17038/files/522401/preview" alt="woman on phone complaining to store about service" width="162" height="142" style="max-width: 100%; height: auto; flex: 0 0 auto;" data-api-endpoint="https://sinclair.instructure.com/api/v1/courses/17038/files/522401" data-api-returntype="File">
  <div style="flex: 1 1 320px; min-width: 240px;">
    <p style="margin-top: 0;">Dissatisfied customers are just a step away from becoming angry customers. It is very important to satisfy these customers - quickly - in order to avoid a nasty confrontation later.</p>
  </div>
</div>

<ul style="margin-left: 24px; padding-left: 24px;">
  <li>Listen. Actively listen and then ask open-ended questions to determine the cause of their frustration. Open-ended questions are questions that cannot be answered with a yes or no. They require explanation.</li>
  <li>Remain positive.</li>
  <li>Smile. Give your name and offer your assistance. Take ownership of the problem. Don't let it go until the problem is solved.</li>
  <li>Don't make excuses for yourself or the company.</li>
  <li>Be compassionate.</li>
  <li>Verify the information you are hearing from them is correct. Verify that you heard correctly and understand what they are saying.</li>
  <li>Take appropriate action.</li>
</ul>

<p>You might have noticed that angry and dissatisfied customers are very much alike. You can take many of the same steps to resolve their problems and turn unhappy customers into loyal customers.</p>

<h4><strong>Indecisive Customers</strong></h4>
<div style="display: flex; align-items: flex-start; gap: 12px; flex-wrap: wrap; margin-bottom: 12px;">
  <img src="https://sinclair.instructure.com/courses/17038/files/522404/preview" alt="Family looking at car to buy" width="180" height="118" style="max-width: 100%; height: auto; flex: 0 0 auto;" data-api-endpoint="https://sinclair.instructure.com/api/v1/courses/17038/files/522404" data-api-returntype="File">
  <div style="flex: 1 1 320px; min-width: 240px;">
    <p style="margin-top: 0;">Indecisive customers truly don't know what they want. (Some of you may be this type of customer, but then maybe you can't make up your mind if you're indecisive or just not sure…) Anyway, these customers can be challenging.</p>
  </div>
</div>

<ul style="margin-left: 24px; padding-left: 24px;">
  <li>Be patient</li>
  <li>Ask open-ended questions</li>
  <li>Listen actively</li>
  <li>Suggest other options/alternatives</li>
  <li>Guide decision making</li>
  <li>Eventually ask closed-ended questions (yes, or no) in order to gain closure</li>
</ul>

<p>These customers need your help in order to make a decision. Find out what they want, and then guide them to a decision. They will thank you.</p>

<h4><strong>Demanding/Domineering Customers</strong></h4>
<div style="display: flex; align-items: flex-start; gap: 12px; flex-wrap: wrap; margin-bottom: 12px;">
  <img src="https://sinclair.instructure.com/courses/17038/files/522402/preview" alt="demanding customer" width="144" height="204" style="max-width: 100%; height: auto; flex: 0 0 auto;" data-api-endpoint="https://sinclair.instructure.com/api/v1/courses/17038/files/522402" data-api-returntype="File">
  <div style="flex: 1 1 320px; min-width: 240px;">
    <p style="margin-top: 0;">For some of you, these will be your most difficult customer type. These people are not mean; they are just incredibly self-assured and know what they want. They really don't need you to help them decide, although sometimes they are wrong and need you to kindly let them in on that little secret. When doing so, be sure not to embarrass them! They will quickly become angry customers!</p>
  </div>
</div>

<ul style="margin-left: 24px; padding-left: 24px;">
  <li>Be professional. These people usually don't have time for much chitchat.</li>
  <li>Respect these customers. Treat them kindly and with respect. Never talk down to them!</li>
  <li>Be firm, fair and focused on what these customers need.</li>
  <li>Tell them quickly what you CAN do; forget what you can't do.</li>
</ul>

<h4><strong>Rude/Inconsiderate Customers</strong></h4>
<p>These people can make you REALLY mad. The trick here is to remain professional and to avoid retaliation. You can never really get even with these people. Take care of their needs and move on to the next customer. Don't let these people ruin your day. Luckily, they are relatively few and far between.</p>

<h4><strong>Talkative Customers</strong></h4>
<p>These people will take all your time. There are other customers waiting for your assistance, but these people will not notice. You need to take care of these people without offending them and help them move on as you take care of other customers.</p>

<ul>
  <li>Remain warm and cordial, but focused on resolving their immediate problem.</li>
  <li>Ask specific open-ended questions.</li>
  <li>Once you've determined their needs, use closed-ended questions to control the conversation.</li>
  <li>Manage the conversation. This takes practice, but you can do it!</li>
</ul>

<h4><strong>Hard to Understand Customers</strong></h4>
<div style="display: flex; align-items: flex-start; gap: 12px; flex-wrap: wrap; margin-bottom: 12px;">
  <img src="https://sinclair.instructure.com/courses/17038/files/522405/preview" alt="customer with computer asking for help on phone" width="148" height="143" style="max-width: 100%; height: auto; flex: 0 0 auto;" data-api-endpoint="https://sinclair.instructure.com/api/v1/courses/17038/files/522405" data-api-returntype="File">
  <div style="flex: 1 1 320px; min-width: 0;">
    <p style="margin-top: 0;">These customers may be hard to understand either because they are hard of hearing, they speak a different language than you, or for a number of other reasons. How will you take care of these customers?</p>
  </div>
</div>

<hr style="border-top: 8px solid #AC1A2F; clear: left;">
```

## Recommended Page Decisions

- Keep and clean the existing Learning Activities pages used in Modules 5, 6, and 7.
- Do not create new Learning Activities pages for Modules 8, 9, or 10 right now.
- Do not create a separate Module 1 Learning Activities page right now. The discussion already carries the video context.
- For consistency, Modules 2, 3, 4, and 13 can use the same checklist treatment as the other BIS modules.
- Remove live links from checklist pages.
- Add the Chapter 2-10 PowerPoints into their modules as optional file items, then keep them in the checklist wording.

## Module 9 Assignment

Recommended approach:

- Keep the assignment title aligned with the template naming pattern, such as `Module 9: Assignment: Identifying Power Skills`.
- Replace the imported D2L dropbox text with the template-style Canvas assignment description below.
- Attach a Canvas rubric based on the two original D2L point-guide lines.

### Canvas Steps

1. Open `Assignments`.
2. Open `Assignment: Identifying Power Skills`.
3. Click `Edit`.
4. Rename it to `Module 9: Assignment: Identifying Power Skills` if you want it to fully match the template naming pattern.
5. Replace the assignment description with the adapted version below.
6. Keep the submission type as `Online` with `File Uploads`.
7. Set the points to `25`.
8. Attach the rubric described below and check `Use this rubric for assignment grading`.
9. Save.

### Adapted Assignment Description

This keeps the original D2L requirements but formats them to match the Canvas assignment template.

```html
<h2 style="color: #ac1a2f; border-bottom: 10px solid #AC1A2F; padding: 10px;">
<img role="presentation" src="https://sinclair.instructure.com/courses/17038/files/516658/preview" alt="" width="45" height="45" data-api-endpoint="https://sinclair.instructure.com/api/v1/courses/17038/files/516658" data-api-returntype="File"><strong>Assignment Overview</strong>
</h2>
<p>In this assignment, you will upload a Word document. Research, identify, and define at least 6 Power Skills, and describe why the skills you chose are important in customer service.</p>
<hr>
<h2>
<img role="presentation" src="https://sinclair.instructure.com/courses/17038/files/516648/download" alt="" width="45" height="45" data-api-endpoint="https://sinclair.instructure.com/api/v1/courses/17038/files/516648" data-api-returntype="File" data-decorative="true"><span style="color: #ac1a2f;"><strong>Instructions</strong></span>
</h2>
<p>Create and submit a Word document that includes the following:</p>
<ul>
  <li>At least 6 Power Skills</li>
  <li>A definition for each Power Skill</li>
  <li>An explanation of why each skill is important in customer service</li>
</ul>
<p>Your assignment will be graded on the completeness of your list, descriptions, and explanation of relevance, as well as grammar, punctuation, and spelling.</p>
<p>Please review the rubric below before submitting your assignment.</p>
<p>Submit your completed Word document to this assignment.</p>
<hr>
<div style="background-color: #f8f8f8; padding: 15px;">
<h2>
<img role="presentation" src="https://sinclair.instructure.com/courses/17038/files/516650/download" alt="" width="45" height="45" data-api-endpoint="https://sinclair.instructure.com/api/v1/courses/17038/files/516650" data-api-returntype="File" data-decorative="true"><strong>Technical Support</strong>
</h2>
<p>Need help using Canvas Assignments? If so, please review the following page: <a href="https://design.instructure.com/courses/178/pages/assignments" target="_blank" rel="noopener" data-api-endpoint="https://design.instructure.com/api/v1/courses/178/pages/assignments" data-api-returntype="Page">Canvas Resources for Students - Assignments.</a></p>
</div>
```

### Suggested Canvas Rubric

The D2L assignment only gave two point-guide lines, not a full rubric. This rubric preserves the original weighting while adding Canvas-friendly rating levels.

Criterion 1 Name: `Complete list with descriptions and relevancy`

Criterion 1 Description: `Includes at least 6 Power Skills, a definition for each one, and an explanation of why each skill is important in customer service.`

Criterion 1 Ratings (`20 points`)

- `Excellent` (`20`): Identifies at least 6 Power Skills, clearly defines each one, and thoroughly explains why each skill is important in customer service.
- `Proficient` (`15`): Identifies at least 6 Power Skills with mostly clear definitions and explanation of relevance, but some detail is missing or uneven.
- `Developing` (`10`): Includes some correct skills and explanations, but the list is incomplete, weakly defined, or only partly connected to customer service relevance.
- `Incomplete` (`0`): Missing, substantially incomplete, or does not address the assignment requirements.

Criterion 2 Name: `Grammar/punctuation/spelling`

Criterion 2 Description: `Demonstrates clear writing with appropriate grammar, punctuation, and spelling for a college-level assignment.`

Criterion 2 Ratings (`5 points`)

- `Excellent` (`5`): Writing is clear and polished with few or no grammar, punctuation, or spelling errors.
- `Proficient` (`3`): Writing has some grammar, punctuation, or spelling errors, but meaning remains clear.
- `Developing` (`1`): Writing has frequent grammar, punctuation, or spelling errors that distract from the content.
- `Incomplete` (`0`): Writing quality seriously interferes with readability or the assignment is not submitted.

Total: `25 points`

## Module 11 Assignment

Recommended approach:

- Keep `What is a Mystery Shop?` as the overview page.
- Keep `Module 11: Discussion: Mystery Shop Part 1` as the planning step.
- Move the content from `Instructions: Mystery Shopper Report` into the `Mystery Shop Paper Dropbox` assignment description.
- Then rename the assignment to something Canvas-native, such as `Mystery Shop Paper`.

### Canvas Steps

1. Open `Assignments`.
2. Open `Mystery Shop Paper Dropbox`.
3. Click `Edit`.
4. Change the assignment name to `Mystery Shop Paper`.
5. Replace the assignment description with the adapted instructions below.
6. In the description, use `Links > Course Link` to link:
   - `What is a Mystery Shop?`
   - `Module 11: Discussion: Mystery Shop Part 1`
   - `Mystery Shop Evaluation Form`
7. Keep the submission type as `Online` with `File Uploads` so students can attach both the report and the completed evaluation form in one submission.
8. Save.
9. After saving, decide whether to:
   - leave `Instructions: Mystery Shopper Report` unpublished as reference, or
   - remove it from the module if the assignment now fully replaces it

Canvas references:

- [How do I add or edit details in an assignment?](https://community.canvaslms.com/t5/Instructor-Guide/How-do-I-add-or-edit-details-in-an-assignment/ta-p/971)
- [How do I create hyperlinks to course or group content in the Rich Content Editor?](https://community.canvaslms.com/t5/Canvas-Basics-Guide/How-do-I-create-hyperlinks-to-course-or-group-content-in-the/ta-p/618247)
- [How do I link to a document from Canvas in the Rich Content Editor?](https://community.canvaslms.com/t5/Canvas-Basics-Guide/How-do-I-link-to-a-document-from-Canvas-in-the-Rich-Content/ta-p/618244)
- [How do I upload a file as an assignment submission in Canvas?](https://community.canvaslms.com/t5/Student-Guide/How-do-I-upload-a-file-as-an-assignment-submission-in-Canvas/ta-p/274)

### Adapted Assignment Description

This keeps the original D2L instructions but changes the D2L/dropbox wording to fit Canvas.

```html
<h2 style="color: #ac1a2f; border-bottom: 10px solid #AC1A2F; padding: 10px;">
<img role="presentation" src="https://sinclair.instructure.com/courses/17038/files/516658/preview" alt="" width="45" height="45" data-api-endpoint="https://sinclair.instructure.com/api/v1/courses/17038/files/516658" data-api-returntype="File"><strong>Assignment Overview</strong>
</h2>
<p>Now that the mystery shop is completed, it is time to write up your report. Create your Mystery Shopper Report in a properly formatted Microsoft Word document and submit it to this assignment.</p>
<p>Before completing this report, review <a title="Module 11: What is a Mystery Shop?" href="https://sinclair.instructure.com/courses/17038/pages/module-11-what-is-a-mystery-shop" data-course-type="wikiPages" data-published="true" data-api-endpoint="https://sinclair.instructure.com/api/v1/courses/17038/pages/module-11-what-is-a-mystery-shop" data-api-returntype="Page"><strong>What is a Mystery Shop?</strong></a>, your <a title="Module 11: Discussion: Mystery Shop Part 1" href="https://sinclair.instructure.com/courses/17038/discussion_topics/23755" data-course-type="discussions" data-published="true" data-api-endpoint="https://sinclair.instructure.com/api/v1/courses/17038/discussion_topics/23755" data-api-returntype="Discussion"><strong>Mystery Shop Part 1 Discussion</strong></a>&nbsp;post, and your completed <a class="instructure_file_link instructure_scribd_file inline_disabled" title="Mystery Shop Evaluation Form.docx" href="https://sinclair.instructure.com/courses/17038/files/516716?wrap=1" target="_blank" rel="noopener" data-api-endpoint="https://sinclair.instructure.com/api/v1/courses/17038/files/516716" data-api-returntype="File"><strong>Mystery Shop Evaluation Form</strong></a>.</p>
<hr>
<h2>
<img role="presentation" src="https://sinclair.instructure.com/courses/17038/files/516648/download" alt="" width="45" height="45" data-api-endpoint="https://sinclair.instructure.com/api/v1/courses/17038/files/516648" data-api-returntype="File" data-decorative="true"><span style="color: #ac1a2f;"><strong>Instructions</strong></span>
</h2>
<p>The first section of your report should provide a summary of the information you provided in your “Before” Mystery Shop discussion. Title this section <strong>Business Overview</strong> and include the business name, why you chose that business, business background, and your planned scenario.</p>
<p>The next section of your report should provide information about your “shopping” experience. Use your completed evaluation sheet as a checklist when discussing each topic. Format your report by providing information under the following headings:</p>
<ul>
  <li><strong>Environment:</strong> date and time of visit, the environment (cleanliness, lighting, safety, layout, comfort)</li>
  <li><strong>Representative:</strong> name, department, attire, grooming, qualities (smiled, eye contact, voice quality, demeanor)</li>
  <li><strong>Service:</strong> specifics about the service you received. Discuss the following:
    <ul>
      <li>What went well during the visit?</li>
      <li>What challenges did you face while visiting the business?</li>
      <li>If the service was great, what did they do specifically to make it great? What steps did they take to go above and beyond basic customer service? Use information learned from the course to include chapter readings, articles, or websites to support your answers.</li>
      <li>If the service was bad, what can the company do to prevent problems like you encountered?</li>
    </ul>
  </li>
</ul>
<p>The final section of your report should provide your <strong>Recommendations</strong>:</p>
<ul>
  <li>What information would you share with the manager/owner of the business about your experience?</li>
  <li>Discuss specific ways that the company could improve service, even if there were no problems found. Use information learned from the course to include chapter readings, articles, or websites to support your suggestions.</li>
  <li>Based on your mystery shop experience, would you continue to shop with the business? Discuss reasons in detail.</li>
  <li>Would you tell your friends and family about the outcome? Will you influence them either to shop or not to shop at this business?</li>
  <li>If you were the manager of the business, what information would you share with the employees?</li>
</ul>
<p>Use proper grammar and correct spelling. Points may be deducted for errors, incomplete sections, and unanswered questions.</p>
<p>Submit your completed Mystery Shopper Report and your completed <a class="instructure_file_link instructure_scribd_file inline_disabled" title="Mystery Shop Evaluation Form.docx" href="https://sinclair.instructure.com/courses/17038/files/516716?wrap=1" target="_blank" rel="noopener" data-api-endpoint="https://sinclair.instructure.com/api/v1/courses/17038/files/516716" data-api-returntype="File"><strong>Mystery Shop Evaluation Form</strong></a>&nbsp;to this assignment.</p>
<hr>
<div style="background-color: #f8f8f8; padding: 15px;">
<h2>
<img role="presentation" src="https://sinclair.instructure.com/courses/17038/files/516650/download" alt="" width="45" height="45" data-api-endpoint="https://sinclair.instructure.com/api/v1/courses/17038/files/516650" data-api-returntype="File" data-decorative="true"><strong>Technical Support</strong>
</h2>
<p>Need help using Canvas Assignments? If so, please review the following page: <a href="https://design.instructure.com/courses/178/pages/assignments" target="_blank" data-api-endpoint="https://design.instructure.com/api/v1/courses/178/pages/assignments" data-api-returntype="Page">Canvas Resources for Students - Assignments.</a></p>
</div>
```

## TTCE Playlist Page Structure

Use one page per TTCE part with a captioned Panopto playlist embed. Since the top-level module already tells students they are in `Through the Customer's Eyes`, the inner structure can use a `Part` heading followed by a single playlist page and then the quiz.

- `Part 1: Why Customer Service Matters`
- `Part 2: What Customers Want`
- `Part 3: Essential Customer Service Skills, Part I`
- `Part 4: Essential Customer Service Skills, Part II`
- `Part 5: Handling Complaints and Dealing with Angry People`
- `Part 6: Service as a Strategic Marketing Tool`

Original D2L TTCE video counts:

- Part 1: 5 videos
- Part 2: 4 videos
- Part 3: 5 videos
- Part 4: 4 videos
- Part 5: 5 videos
- Part 6: 4 videos

Recommended naming pattern for the playlist pages:

- `TTCE 1: Videos: Chapter 1-5`
- `TTCE 2: Videos: Chapter 1-4`
- `TTCE 3: Videos: Chapter 1-5`
- `TTCE 4: Videos: Chapter 1-4`
- `TTCE 5: Videos: Chapter 1-4`
- `TTCE 6: Videos: Chapter 1-4`

### Generic TTCE Playlist Page

This follows the Learning Activities style, but keeps each page focused on one captioned Panopto playlist for the full TTCE part.

```html
<hr style="border-top: 10px solid #AC1A2F; border-bottom: none; margin: 0 0 16px 0;">
<h2><strong><span style="color: #ac1a2f;"><img role="presentation" src="https://sinclair.instructure.com/courses/17038/files/516665/preview" alt="" width="45" data-api-endpoint="https://sinclair.instructure.com/api/v1/courses/17038/files/516665" data-api-returntype="File">Watch</span></strong></h2>
<p>Watch the captioned playlist below for this section of <strong>Through the Customer's Eyes</strong>.</p>
<h3>[Playlist Title]</h3>
<div>
  [Paste Panopto playlist embed code here]
</div>
<hr>
<h2><strong><span style="color: #ac1a2f;"><img role="presentation" src="https://sinclair.instructure.com/courses/17038/files/516656/preview" alt="" width="45" data-api-endpoint="https://sinclair.instructure.com/api/v1/courses/17038/files/516656" data-api-returntype="File">Do This</span></strong></h2>
<p>After watching all videos in this section, return to the module and complete the corresponding quiz.</p>
<hr style="border-top: 8px solid #AC1A2F;">
```
