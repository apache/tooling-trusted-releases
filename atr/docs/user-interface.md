# 3.5. User interface

**Up**: `3.` [Developer guide](developer-guide)

**Prev**: `3.4.` [Storage interface](storage-interface)

**Next**: `3.6.` [Tasks](tasks)

**Sections**:

* [Introduction](#introduction)
* [Jinja2 templates](#jinja2-templates)
* [Forms](#forms)
* [Programmatic HTML](#programmatic-html)
* [The htm.Block class](#the-htmblock-class)
* [How a route renders UI](#how-a-route-renders-ui)

## Introduction

ATR uses server-side rendering almost exclusively: the server generates HTML and sends it to the browser, which displays it. We try to avoid client-side scripting, and in the rare cases where we need dynamic front end components we use plain TypeScript without recourse to any third party framework. (We have some JavaScript too, but we aim to use TypeScript only.) Sometimes we incur a full page load where perhaps it would be more ideal to update a fragment of the DOM in place, but page loads are very fast in modern browsers, so this is less of an issue than it would have been a decade ago.

The UI is built from three main pieces: [Jinja2](https://jinja.palletsprojects.com/) for templates, [WTForms](https://wtforms.readthedocs.io/) for HTML forms, and [htpy](https://htpy.dev/) for programmatic HTML generation. We style everything with [Bootstrap](https://getbootstrap.com/), which we customize slightly.

## Jinja2 templates

Templates live in [`templates/`](/ref/atr/templates/). Each template is a Jinja2 file that defines HTML structure with placeholders for dynamic content. Route handlers render templates by calling [`template.render`](/ref/atr/template.py:render), which is an alias for [`template.render_sync`](/ref/atr/template.py:render_sync). The function is asynchronous and takes a template name plus keyword arguments for the template variables.

Here is an example from [`get/keys.py`](/ref/atr/get/keys.py:add):

```python
return await template.render(
    "keys-add.html",
    asf_id=session.uid,
    user_committees=participant_of_committees,
    form=form,
    key_info=key_info,
    algorithms=shared.algorithms,
)
```

The template receives these variables and can access them directly. If you pass a variable called `form`, the template can use `{{ form }}` to render it. [Jinja2 has control structures](https://jinja.palletsprojects.com/en/stable/templates/#list-of-control-structures) like `{% for %}` and `{% if %}`, which you use when iterating over data or conditionally showing content.

Templates are loaded into memory at server startup by [`preload.setup_template_preloading`](/ref/atr/preload.py:setup_template_preloading). This means that changing a template requires restarting the server in development, which can be configured to happen automatically, but it also means that rendering is fast because we never do a disk read during request handling. The preloading scans [`templates/`](/ref/atr/templates/) recursively and caches every file.

Template rendering happens in a thread pool to avoid blocking the async event loop. The function [`_render_in_thread`](/ref/atr/template.py:_render_in_thread) uses `asyncio.to_thread` to execute Jinja2's synchronous `render` method.

## Forms

HTML forms in ATR are handled by [Pydantic](https://docs.pydantic.dev/latest/) models accessed through our [`form`](/ref/atr/form.py) module. Each form is a class that inherits from [`form.Form`](/ref/atr/form.py:Form), which itself inherits from `pydantic.BaseModel`. Form fields are defined as class attributes using Pydantic type annotations, with the [`form.label`](/ref/atr/form.py:label) function providing field metadata like labels and documentation.

Here is a typical form definition from [`shared/keys.py`](/ref/atr/shared/keys.py):

```python
class AddOpenPGPKeyForm(form.Form):
    public_key: str = form.label(
        "Public OpenPGP key",
        'Your public key should be in ASCII-armored format, starting with "-----BEGIN PGP PUBLIC KEY BLOCK-----"',
        widget=form.Widget.TEXTAREA,
    )
    selected_committees: form.StrList = form.label(
        "Associate key with committees",
        "Select the committees with which to associate your key.",
    )

    @pydantic.model_validator(mode="after")
    def validate_at_least_one_committee(self) -> "AddOpenPGPKeyForm":
        if not self.selected_committees:
            raise ValueError("You must select at least one committee to associate with this key")
        return self
```

### Field types and labels

The [`form.label`](/ref/atr/form.py:label) function is used to add metadata to Pydantic fields. The first argument is the label text, the second (optional) argument is documentation text that appears below the field, and you can pass additional keyword arguments like `widget=form.Widget.TEXTAREA` to specify the HTML widget type.

Fields use Pydantic type annotations to define their data type:

* `str` - text input (default widget: `Widget.TEXT`)
* `form.Email` - email input with validation
* `form.URL` - URL input with validation
* `form.Bool` - checkbox
* `form.Int` - number input
* `form.StrList` - multiple checkboxes that collect strings
* `form.File` - single file upload
* `form.FileList` - multiple file upload
* `form.Enum[EnumType]` - dropdown select from enum values
* `form.Set[EnumType]` - multiple checkboxes from enum values

Empty values for fields are allowed by default in most cases, but URL is an exception.

The `widget` parameter in [`form.label`](/ref/atr/form.py:label) lets you override the default widget for a field type. Available widgets include: `TEXTAREA`, `CHECKBOXES`, `SELECT`, `RADIO`, `HIDDEN`, and others from the `form.Widget` enum. Common reasons to override:

* HIDDEN: for values passed from the route, not entered by the user
* TEXTAREA: for multi-line text input
* RADIO: for mutually exclusive choices
* CUSTOM: for fully custom rendering

From [`projects.AddProjectForm`](/ref/atr/shared/projects.py:AddProjectForm):

```python
committee_name: str = form.label("Committee name", widget=form.Widget.HIDDEN)
```

From [`resolve.SubmitForm`](/ref/atr/shared/resolve.py:SubmitForm):

```python
email_body: str = form.label("Email body", widget=form.Widget.TEXTAREA)
```

From [`resolve.SubmitForm`](/ref/atr/shared/resolve.py:SubmitForm):

```python
vote_result: Literal["Passed", "Failed"] = form.label("Vote result", widget=form.Widget.RADIO)
```

From [`vote.CastVoteForm`](/ref/atr/shared/vote.py:CastVoteForm):

```python
decision: Literal["+1", "0", "-1"] = form.label("Your vote", widget=form.Widget.CUSTOM)
```

### Using forms in routes

To use a form in a route, use the [`@post.committer()`](/ref/atr/blueprints/post.py:committer) decorator to get the session and auth the user, and the [`@post.form()`](/ref/atr/blueprints/post.py:form) decorator to parse and validate input data:

```python
@post.committer("/keys/add")
@post.form(shared.keys.AddOpenPGPKeyForm)
async def add(session: web.Committer, add_openpgp_key_form: shared.keys.AddOpenPGPKeyForm) -> web.WerkzeugResponse:
    """Add a new public signing key to the user's account."""
    try:
        key_text = add_openpgp_key_form.public_key
        selected_committee_names = add_openpgp_key_form.selected_committees

        # Process the validated form data...
        async with storage.write() as write:
            # ...

        await quart.flash("OpenPGP key added successfully.", "success")
    except web.FlashError as e:
        await quart.flash(str(e), "error")
    except Exception as e:
        log.exception("Error adding OpenPGP key:")
        await quart.flash(f"An unexpected error occurred: {e!s}", "error")

    return await session.redirect(get.keys.keys)
```

The [`form.validate`](/ref/atr/form.py:validate) function should only be called manually when the request comes from JavaScript, as in [`announce_preview`](/ref/atr/post/preview.py:announce_preview). It takes the form class, the form data dictionary, and an optional context dictionary. If validation succeeds, it returns an instance of your form class with validated data. If validation fails, it raises a `pydantic.ValidationError`.

The error handling uses [`form.flash_error_data`](/ref/atr/form.py:flash_error_data) to prepare error information for display, and [`form.flash_error_summary`](/ref/atr/form.py:flash_error_summary) to create a user-friendly summary of all validation errors.

### Rendering forms

The `form` module provides the [`form.render`](/ref/atr/form.py:render) function (or [`form.render_block`](/ref/atr/form.py:render_block) for use with [`htm.Block`](/ref/atr/htm.py:Block)) that generates Bootstrap-styled HTML. This function creates a two-column layout with labels on the left and inputs on the right:

```python
form.render_block(
    page,
    model_cls=shared.keys.AddOpenPGPKeyForm,
    action=util.as_url(post.keys.add),
    submit_label="Add OpenPGP key",
    cancel_url=util.as_url(keys),
    defaults={
        "selected_committees": committee_choices,
    },
)
```

The `defaults` parameter accepts a dictionary to populate initial field values. For checkbox/radio groups and select dropdowns, you can pass a list of `(value, label)` tuples to dynamically provide choices. The `render` function returns htpy elements which you can embed in templates or return directly from route handlers.

Key rendering parameters:

* `action` - form submission URL (defaults to current path)
* `submit_label` - text for the submit button
* `cancel_url` - if provided, adds a cancel link next to submit
* `defaults` - dictionary of initial values or dynamic choices
* `textarea_rows` - number of rows for textarea widgets (default: 12)
* `wider_widgets` - use wider input column (default: False)
* `border` - add borders between fields (default: False)

## Programmatic HTML

Sometimes you need to generate HTML in Python rather than in a template. For this we use [htpy](https://htpy.dev/), which provides a Python API for building HTML elements. You import `htpy` and then use it like this:

```python
import htpy

element = htpy.div(".container")[
    htpy.h1["Release Candidate"],
    htpy.p["This is a release candidate."],
]
```

The square brackets syntax is how htpy accepts children. The parentheses syntax is for attributes. If you want a div with an id, you write `htpy.div(id="content")`. If you want a div with a class, you can use CSS selector syntax like `htpy.div(".my-class")` or you can use `htpy.div(class_="my-class")`, remembering to use the underscore in `class_`.

You can nest elements arbitrarily, mix strings and elements, and pass lists of elements. Converting an htpy element to a string renders it as HTML. Templates can therefore render htpy elements directly by passing them as variables.

The htpy library provides type annotations for HTML elements. It does not validate attribute names or values, so you can pass nonsensical attributes without error. We plan to fix this by adding stricter types in our `htm` wrapper. The main benefit to using `htpy` (via `htm`) is having a clean Python API for HTML generation rather than concatenating strings or using templating.

## The htm.Block class

The ATR [`htm`](/ref/atr/htm.py) module extends htpy with a [`Block`](/ref/atr/htm.py:Block) class that makes it easier to build complex HTML structures incrementally. You create a block, append elements to it, and then collect them into a final element. Here is the typical usage pattern:

```python
import atr.htm as htm

div = htm.Block()
div.h1["Release Information"]
div.p["The release was created on ", release.created.isoformat(), "."]
if release.released:
    div.p["It was published on ", release.released.isoformat(), "."]
return div.collect()
```

The block class provides properties for common HTML elements like `h1`, `h2`, `p`, `div`, `ul`, and so on. When you access these properties, you get back a [`BlockElementCallable`](/ref/atr/htm.py:BlockElementCallable), which you can call to create an element with attributes or use square brackets to add grandchildren of the block. The element is automatically appended to the block's internal list of children.

The `collect` method assembles all of the elements into a single htpy element. If you created the block with an outer element like `htm.Block(htpy.div(".container"))`, that element wraps all the children. If you created the block with no outer element, `collect` wraps everything in a div. You can also pass a `separator` argument to `collect`, which inserts a text separator between elements.

The block class is useful when you are building HTML in a loop or when you have conditional elements. Instead of managing a list of elements manually, you can let the block class do it for you: append elements as you go, and at the end call `collect` to get the final result. This is cleaner than concatenating strings or maintaining lists yourself.

The block class also adds a `data-src` attribute to elements, which records which function created the element. If you see an element in the browser inspector with `data-src="atr.get.keys:keys"`, you know that it came from the `keys` function in `get/keys.py`. The source is extracted automatically using [`log.caller_name`](/ref/atr/log.py:caller_name).

## How a route renders UI

A typical route that renders UI first authenticates the user, loads data from the database, builds HTML using htpy, and renders it using a template. GET and POST requests are handled by separate routes, with form validation automatically handled by the [`@post.form()`](/ref/atr/blueprints/post.py:form) decorator. Here is a simplified example from [`get/keys.py`](/ref/atr/get/keys.py:add):

```python
@get.committer("/keys/add")
async def add(session: web.Committer) -> str:
    """Add a new public signing key to the user's account."""
    async with storage.write() as write:
        participant_of_committees = await write.participant_of_committees()

    committee_choices = [(c.name, c.display_name or c.name) for c in participant_of_committees]

    page = htm.Block()
    page.p[htm.a(".atr-back-link", href=util.as_url(keys))["← Back to Manage keys"]]
    page.div(".my-4")[
        htm.h1(".mb-4")["Add your OpenPGP key"],
        htm.p["Add your public key to use for signing release artifacts."],
    ]

    form.render_block(
        page,
        model_cls=shared.keys.AddOpenPGPKeyForm,
        action=util.as_url(post.keys.add),
        submit_label="Add OpenPGP key",
        cancel_url=util.as_url(keys),
        defaults={
            "selected_committees": committee_choices,
        },
    )
    ...
    return await template.blank(
        "Add your OpenPGP key",
        content=page.collect(),
        description="Add your public signing key to your ATR account.",
    )
```

The route is decorated with [`@get.committer()`](/ref/atr/blueprints/get.py:committer), which handles authentication and provides a `session` object that is an instance of [`web.Committer`](/ref/atr/web.py:Committer) with a range of useful properties and methods.

The function builds the UI using an [`htm.Block`](/ref/atr/htm.py:Block) object, which provides a convenient API for incrementally building HTML. The form is rendered directly into the block using [`form.render_block()`](/ref/atr/form.py:render_block), which generates all the necessary HTML with Bootstrap styling.

Finally, the route returns the rendered HTML using [`template.blank()`](/ref/atr/template.py:blank), which renders a minimal template with just a title and content area.

Form submission is handled by a separate POST route:

```python
@post.committer("/keys/add")
@post.form(shared.keys.AddOpenPGPKeyForm)
async def add(session: web.Committer, add_openpgp_key_form: shared.keys.AddOpenPGPKeyForm) -> web.WerkzeugResponse:
    """Add a new public signing key to the user's account."""
    try:
        key_text = add_openpgp_key_form.public_key
        selected_committee_names = add_openpgp_key_form.selected_committees

        # Process the validated form data...

        await quart.flash("OpenPGP key added successfully.", "success")
    except web.FlashError as e:
        await quart.flash(str(e), "error")

    return await session.redirect(get.keys.keys)
```

The [`@post.form()`](/ref/atr/blueprints/post.py:form) decorator handles form validation automatically. If validation fails, it flashes error messages and redirects back to the GET route. If validation succeeds, the validated form instance is injected into the route handler as a parameter.

Bootstrap CSS classes are applied automatically by the form rendering functions. The functions use classes like `form-control`, `form-select`, `btn-primary`, `is-invalid`, and `invalid-feedback`. We currently use Bootstrap 5. If you generate HTML manually with htpy, you can apply Bootstrap classes yourself by using the CSS selector syntax like `htpy.div(".container")` or the class attribute like `htpy.div(class_="container")`.
