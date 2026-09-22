# 1.2. Project configuration

**Up**: `1.` [Introduction to ATR](introduction-to-atr)

**Prev**: `1.1.` [Getting started](getting-started)

**Next**: `1.3.` [Release manager setup](release-manager-setup)

**Sections**:

* [Project configuration](#project-configuration)

## Project configuration

You can manage project metadata in ATR or in the `project` block of your repository's `.asf.yaml` file. Committee members can use **Export .asf.yaml** on the project page to get the current configuration as a starting point.

For example, the Maven Filtering project has the following metadata:

```yaml
project:
  metadata:
    key: maven-filtering
    committee: maven
    name: Apache Maven Filtering
```

`key` is the project identifier used in ATR URLs and API requests. `committee` identifies the committee responsible for the project. `name` is the project's full display name and must start with `Apache` and a space. The web form adds this prefix automatically, but `.asf.yaml` synchronization does not.

A name must be supplied when creating a project. When updating an existing project, omitting the name preserves its current value. If you use `doap:` to supply metadata, the name comes from the DOAP project's `<name>` element and must include the prefix there. A `name` supplied alongside `doap:` is ignored.

Synchronization is enabled by default and runs from the repository's default branch. When enabled, the next push imports the supplied values, overwriting manual changes to those fields in ATR. Correct the name in `.asf.yaml` or its linked DOAP file so that subsequent imports preserve the correction.

See the [asfyaml project metadata reference](https://github.com/apache/infrastructure-asfyaml#project) for the complete configuration format, release policy settings, and synchronization options.
