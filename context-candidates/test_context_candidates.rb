# frozen_string_literal: true

require "minitest/autorun"
require "tmpdir"
require "fileutils"
require "json"
require "open3"

TOOL = File.expand_path("context-candidates", __dir__)

class ContextCandidatesCliTest < Minitest::Test
  def setup
    @dir = Dir.mktmpdir("context-candidates-test")
    sh("git", "init", "-q", @dir)
    sh("git", "-C", @dir, "config", "user.email", "test@example.com")
    sh("git", "-C", @dir, "config", "user.name", "Test")

    write("AGENTS.md", "# Rules\nUse Rails conventions.\n")
    write("app/models/draft.rb", <<~RB)
      class Draft < ApplicationRecord
        validates :title, presence: true
      end
    RB
    write("app/controllers/drafts_controller.rb", <<~RB)
      class DraftsController < ApplicationController
        def update
          @draft.update!(draft_params)
        end

        private

        def draft_params
          params.require(:draft).permit(:title)
        end
      end
    RB
    write("app/views/drafts/_form.html.erb", <<~ERB)
      <%= form_with model: draft do |f| %>
        <%= f.text_field :title %>
        <%= f.submit "Generate Listing" %>
      <% end %>
    ERB
    write("test/models/draft_test.rb", "class DraftTest < ActiveSupport::TestCase\nend\n")
    write("config/routes.rb", "Rails.application.routes.draw { resources :drafts }\n")
    write("db/schema.rb", <<~RB)
      ActiveRecord::Schema[8.0].define do
        create_table "drafts" do |t|
          t.string "title"
        end
      end
    RB
    write("docs/decisions/001-drafts.md", "# Draft persistence\nDraft state lives in drafts.\n")

    sh("git", "-C", @dir, "add", ".")
    sh("git", "-C", @dir, "commit", "-qm", "initial")
  end

  def teardown
    FileUtils.remove_entry(@dir)
  end

  def test_classifies_source_tests_contracts_decisions_and_instructions
    result = run_tool
    paths = result.fetch("candidates").transform_values { |items| items.map { |i| i.fetch("path") } }

    assert_includes paths.fetch("primary"), "app/models/draft.rb"
    assert_includes paths.fetch("primary"), "app/controllers/drafts_controller.rb"
    assert_includes paths.fetch("tests"), "test/models/draft_test.rb"
    assert_includes paths.fetch("contracts"), "db/schema.rb"
    assert_includes paths.fetch("contracts"), "config/routes.rb"
    assert_includes paths.fetch("decisions"), "docs/decisions/001-drafts.md"
    assert_equal ["AGENTS.md"], paths.fetch("instructions")

    refute_includes paths.fetch("primary"), "db/schema.rb"
    refute_includes paths.fetch("primary"), "config/routes.rb"
  end

  def test_output_is_stable_for_same_worktree
    first = run_tool
    second = run_tool
    assert_equal first, second
  end

  def test_ignores_untracked_files
    write("app/models/category.rb", "class Category; end\n")
    result = run_tool
    all_paths = result.fetch("candidates").values.flatten.map { |i| i.fetch("path") }
    refute_includes all_paths, "app/models/category.rb"
  end

  def test_discovers_html_and_css_as_production_source
    write("app/static/index.html", "<select aria-label='Category'></select>\n")
    write("app/static/styles.css", ".category { display: grid; }\n")
    sh("git", "-C", @dir, "add", ".")

    primary = run_tool.fetch("candidates").fetch("primary").map { |item| item.fetch("path") }
    assert_includes primary, "app/static/index.html"
    assert_includes primary, "app/static/styles.css"
  end

  def test_reads_full_block_yaml_story_clauses
    story = <<~YAML
      story:
        normalized_clauses:
          - id: BEH-1
            text: >
              Select waterproof garments
              with durable seams.
          - id: BEH-2
            text: Select velveteen garments.
    YAML
    task = "id: T1\nstory_clauses: [BEH-1]\ndependencies: []\n"

    result = run_tool(task: task, story: story)
    assert_includes result.fetch("requirements").fetch("story"), "Select waterproof garments with durable seams."
    assert_includes result.fetch("search").fetch("terms"), "waterproof"
    refute_includes result.fetch("search").fetch("terms"), "velveteen"
  end

  def test_sibling_mentions_are_not_dependencies
    write("notes/quartz.md", "Reference material.\n")
    sh("git", "-C", @dir, "add", ".")
    task = { id: "T1", dependencies: [], non_goals: ["T2 owns quartz"] }.to_json
    siblings = { tasks: [{ id: "T1", title: "quartz" }, { id: "T2", title: "quartz" }] }.to_json

    result = run_tool(task: task, story_tasks: siblings)
    assert_empty result.fetch("candidates").fetch("prerequisites")
  end

  def test_only_declared_prerequisite_records_supply_candidates
    %w[quartz amber opal].each { |name| write("notes/#{name}.md", "Reference material for #{name}.\n") }
    sh("git", "-C", @dir, "add", ".")
    task = { id: "T2", title: "amber", dependencies: ["T1"], non_goals: ["T3 owns opal"] }.to_json
    siblings = { tasks: [{ id: "T1", title: "quartz" }, { id: "T2", title: "amber" }, { id: "T3", title: "opal" }] }.to_json

    result = run_tool(task: task, story_tasks: siblings)
    paths = result.fetch("candidates").fetch("prerequisites").map { |item| item.fetch("path") }
    assert_equal ["notes/quartz.md"], paths
  end

  def test_match_limit_also_limits_snippets
    lines = Array.new(100) { "# filler\n" }
    [0, 30, 60, 90].each { |index| lines[index] = "# quartz\n" }
    write("app/quartz.rb", lines.join)
    sh("git", "-C", @dir, "add", ".")

    result = run_tool(task: "quartz", story: "quartz", extra_options: ["--max-matches-per-file", "1", "--snippet-context", "0"])
    candidate = result.fetch("candidates").fetch("primary").find { |item| item.fetch("path") == "app/quartz.rb" }
    assert_equal 1, candidate.fetch("score_evidence").length
    assert_equal 1, candidate.fetch("snippets").length
    assert_equal 1, candidate.fetch("snippets").first.fetch("start_line")
    assert_equal 1, candidate.fetch("snippets").first.fetch("end_line")
  end

  def test_prefix_named_test_is_not_production_source
    write("tools/test_category.rb", "# category draft\n")
    sh("git", "-C", @dir, "add", ".")

    categories = run_tool.fetch("candidates")
    assert_includes categories.fetch("tests").map { |item| item.fetch("path") }, "tools/test_category.rb"
    refute_includes categories.fetch("primary").map { |item| item.fetch("path") }, "tools/test_category.rb"
  end

  def test_resolves_bare_require_relative_without_following_bare_require
    write("app/models/draft.rb", "require_relative 'helper'\nrequire 'external'\n# category draft\n")
    write("app/models/helper.rb", "# Utility\n")
    write("app/models/external.rb", "# External library lookalike\n")
    sh("git", "-C", @dir, "add", ".")

    dependencies = run_tool.fetch("candidates").fetch("dependencies").map { |item| item.fetch("path") }
    assert_includes dependencies, "app/models/helper.rb"
    refute_includes dependencies, "app/models/external.rb"
  end

  def test_structural_keys_do_not_become_search_terms
    write("app/quartz.rb", "# waterproof\n")
    write("app/noise.rb", "# text\n" * 100)
    sh("git", "-C", @dir, "add", ".")
    task = { id: "T1", story_clauses: ["BEH-1"], dependencies: [] }.to_json
    story = { story: { normalized_clauses: [{ id: "BEH-1", text: "waterproof" }] } }.to_json

    result = run_tool(task: task, story: story, extra_options: ["--max-files", "1"])
    assert_equal ["app/quartz.rb"], result.fetch("candidates").fetch("primary").map { |item| item.fetch("path") }
    %w[text story_clauses normalized_clauses dependencies].each do |key|
      refute_includes result.fetch("search").fetch("terms"), key
    end
  end

  def test_resolves_repository_qualified_python_modules_and_packages
    write("app/entry.py", "from app.quartz import helper\nimport app.citrine\n# category draft\n")
    write("app/quartz.py", "# Utility\n")
    write("app/citrine/__init__.py", "# Utility\n")
    sh("git", "-C", @dir, "add", ".")

    dependencies = run_tool.fetch("candidates").fetch("dependencies").map { |item| item.fetch("path") }
    assert_includes dependencies, "app/quartz.py"
    assert_includes dependencies, "app/citrine/__init__.py"
  end

  def test_excludes_tracked_symlink_dependencies
    Dir.mktmpdir("context-candidates-external") do |external|
      File.write(File.join(external, "quartz.py"), "# PRIVATE_LINK_SENTINEL\n")
      write("app/entry.py", "from .quartz import helper\n# category draft\n")
      File.symlink(File.join(external, "quartz.py"), File.join(@dir, "app/quartz.py"))
      sh("git", "-C", @dir, "add", ".")

      result = run_tool
      paths = result.fetch("candidates").values.flatten.map { |item| item.fetch("path") }
      refute_includes paths, "app/quartz.py"
      refute_includes JSON.generate(result), "PRIVATE_LINK_SENTINEL"
    end
  end

  def test_excludes_tracked_files_under_a_symlinked_directory
    Dir.mktmpdir("context-candidates-external") do |external|
      File.write(File.join(external, "quartz.py"), "# category PRIVATE_DIRECTORY_SENTINEL\n")
      write("app/entry.py", "from .helpers.quartz import helper\n# category draft\n")
      write("app/helpers/quartz.py", "# Utility\n")
      sh("git", "-C", @dir, "add", ".")
      FileUtils.remove_entry(File.join(@dir, "app/helpers"))
      File.symlink(external, File.join(@dir, "app/helpers"))

      result = run_tool
      paths = result.fetch("candidates").values.flatten.map { |item| item.fetch("path") }
      refute_includes paths, "app/helpers/quartz.py"
      refute_includes JSON.generate(result), "PRIVATE_DIRECTORY_SENTINEL"
    end
  end

  def test_only_declared_story_clauses_supply_story_text
    task = { id: "T1", story_clauses: ["BEH-1"], non_goals: ["BEH-2 belongs to another task"] }.to_json
    story = { story: { normalized_clauses: [{ id: "BEH-1", text: "waterproof" }, { id: "BEH-2", text: "velveteen" }] } }.to_json

    result = run_tool(task: task, story: story)
    assert_equal ["BEH-1"], result.fetch("requirements").fetch("referenced_story_ids")
    assert_includes result.fetch("search").fetch("terms"), "waterproof"
    refute_includes result.fetch("search").fetch("terms"), "velveteen"

    empty = run_tool(task: { story_clauses: [], non_goals: ["BEH-2"] }.to_json, story: story)
    assert_empty empty.fetch("requirements").fetch("referenced_story_ids")
    assert_equal "", empty.fetch("requirements").fetch("story")
  end

  def test_resolves_javascript_imports_with_and_without_extensions
    write("app/entry.js", "import { helper } from './quartz.js';\nimport helper2 from './citrine';\n// category draft\n")
    write("app/quartz.js", "// Utility\n")
    write("app/citrine.js", "// Utility\n")
    sh("git", "-C", @dir, "add", ".")

    dependencies = run_tool.fetch("candidates").fetch("dependencies").map { |item| item.fetch("path") }
    assert_includes dependencies, "app/quartz.js"
    assert_includes dependencies, "app/citrine.js"
  end

  def test_resolves_submodules_in_python_from_package_imports
    write("app/entry.py", "from app import quartz as helper\nfrom . import (\n  citrine, # helper\n  amber as alias,\n)\n# category draft\n")
    write("app/__init__.py", "# Utility\n")
    %w[quartz citrine amber].each { |name| write("app/#{name}.py", "# Utility\n") }
    sh("git", "-C", @dir, "add", ".")

    dependencies = run_tool.fetch("candidates").fetch("dependencies").map { |item| item.fetch("path") }
    %w[app/__init__.py app/quartz.py app/citrine.py app/amber.py].each do |path|
      assert_includes dependencies, path
    end
  end

  def test_dependency_limit_uses_only_retained_primary_sources
    write("app/first.py", "from app.zulu import helper\n" + "# category\n" * 20)
    write("app/second.py", "from app.amber import helper\n# category\n")
    write("app/zulu.py", "# Utility\n")
    write("app/amber.py", "# Utility\n")
    sh("git", "-C", @dir, "add", ".")

    result = run_tool(task: "category", story: "category", extra_options: ["--max-files", "1"])
    categories = result.fetch("candidates")
    assert_equal ["app/first.py"], categories.fetch("primary").map { |item| item.fetch("path") }
    assert_equal ["app/zulu.py"], categories.fetch("dependencies").map { |item| item.fetch("path") }
  end

  def test_scans_dependencies_beyond_the_twelfth_retained_source
    13.times do |index|
      name = format("%02d", index + 1)
      write("app/entry#{name}.py", "from app.utility#{name} import helper\n# category\n")
      write("app/utility#{name}.py", "# Utility\n")
    end
    sh("git", "-C", @dir, "add", ".")

    result = run_tool(task: "category", story: "category")
    dependencies = result.fetch("candidates").fetch("dependencies").map { |item| item.fetch("path") }
    assert_equal 13, dependencies.length
    assert_includes dependencies, "app/utility13.py"
  end

  def test_resolves_multiline_javascript_imports
    write("app/entry.js", "import {\n  helper,\n  second as alias\n} from './quartz.js';\nimport './citrine.js';\n// category\n")
    write("app/quartz.js", "// Utility\n")
    write("app/citrine.js", "// Utility\n")
    sh("git", "-C", @dir, "add", ".")

    result = run_tool(task: "category", story: "category")
    dependencies = result.fetch("candidates").fetch("dependencies").map { |item| item.fetch("path") }
    assert_includes dependencies, "app/quartz.js"
    assert_includes dependencies, "app/citrine.js"
  end

  def test_resolves_each_comma_separated_python_import
    write("app/entry.py", "import app.quartz as helper, app.citrine\n# category\n")
    write("app/quartz.py", "# Utility\n")
    write("app/citrine.py", "# Utility\n")
    sh("git", "-C", @dir, "add", ".")

    result = run_tool(task: "category", story: "category")
    dependencies = result.fetch("candidates").fetch("dependencies").map { |item| item.fetch("path") }
    assert_includes dependencies, "app/quartz.py"
    assert_includes dependencies, "app/citrine.py"
  end

  def test_includes_instructions_for_tests_contracts_and_dependencies
    write("tests/AGENTS.md", "# Test rules\n")
    write("tests/test_category.rb", "# category draft\n")
    write("db/AGENTS.md", "# Contract rules\n")
    write("app/services/AGENTS.md", "# Dependency rules\n")
    write("app/services/quartz.rb", "# Utility\n")
    write("app/models/draft.rb", "require_relative '../services/quartz'\n# category draft\n")
    sh("git", "-C", @dir, "add", ".")

    categories = run_tool.fetch("candidates")
    instructions = categories.fetch("instructions").map { |item| item.fetch("path") }
    %w[AGENTS.md tests/AGENTS.md db/AGENTS.md app/services/AGENTS.md].each do |path|
      assert_includes instructions, path
    end
    assert_includes categories.fetch("tests").map { |item| item.fetch("path") }, "tests/test_category.rb"
    assert_includes categories.fetch("contracts").map { |item| item.fetch("path") }, "db/schema.rb"
    assert_includes categories.fetch("dependencies").map { |item| item.fetch("path") }, "app/services/quartz.rb"
    all_paths = categories.values.flatten.map { |item| item.fetch("path") }
    assert_equal all_paths.uniq, all_paths
  end

  def test_skips_candidates_whose_instructions_exceed_the_cap
    write("tests/AGENTS.md", "# Test rules\n")
    write("tests/test_category.rb", "# category\n")
    sh("git", "-C", @dir, "add", ".")

    result = run_tool(task: "category", story: "category", extra_options: ["--max-files", "1"])
    paths = result.fetch("candidates").values.flatten.map { |item| item.fetch("path") }
    refute_includes paths, "tests/test_category.rb"
    assert_includes paths, "AGENTS.md"
  end

  def test_resolves_go_imports_against_the_declared_module
    write("go.mod", "module github.com/acme/widget\n\ngo 1.22\n")
    write("cmd/app/main.go", "package main\nimport (\n  \"github.com/acme/widget/internal/quartz\"\n  \"example.com/external\"\n)\n// category\n")
    write("internal/quartz/helper.go", "package quartz\n")
    write("external/helper.go", "package external\n")
    sh("git", "-C", @dir, "add", ".")

    result = run_tool(task: "category", story: "category")
    dependencies = result.fetch("candidates").fetch("dependencies").map { |item| item.fetch("path") }
    assert_includes dependencies, "internal/quartz/helper.go"
    refute_includes dependencies, "external/helper.go"
  end

  def test_prerequisites_use_implementation_content_and_exclude_task_artifacts
    first = { id: "T1", title: "quartz", required_behavior: ["quartz"], dependencies: [] }
    second = { id: "T2", title: "category", required_behavior: ["category"], dependencies: ["T1"] }
    later = { id: "T3", title: "quartz", required_behavior: ["quartz"], dependencies: ["T2"] }
    siblings = { tasks: [first, second, later] }
    write("stories/current.md", second.to_json)
    write("stories/prior.md", first.to_json)
    write("stories/later.md", later.to_json)
    write("stories/bundle.yaml", siblings.to_json)
    write("app/utility.rb", "# quartz\n")
    write("notes/quartz.md", "Reference material without matching content.\n")
    sh("git", "-C", @dir, "add", ".")

    result = run_tool(task: File.join(@dir, "stories/current.md"), story_tasks: File.join(@dir, "stories/bundle.yaml"))
    paths = result.fetch("candidates").fetch("prerequisites").map { |item| item.fetch("path") }
    assert_equal ["app/utility.rb"], paths
  end

  def test_binary_test_fixtures_do_not_break_json_output
    write("tests/category.bin", "\xFF\x00PRIVATE_BINARY_SENTINEL".b)
    sh("git", "-C", @dir, "add", ".")

    result = run_tool
    candidate = result.fetch("candidates").fetch("tests").find { |item| item.fetch("path") == "tests/category.bin" }
    refute_nil candidate
    assert_empty candidate.fetch("snippets")
    refute_includes JSON.generate(result), "PRIVATE_BINARY_SENTINEL"
  end

  def test_backfills_sources_and_tests_when_scoped_instructions_do_not_fit
    write("app/nested/AGENTS.md", "# Nested rules\n")
    write("app/nested/first.py", "# category\n" * 20)
    write("app/second.py", "# category\n")
    write("aaa/AGENTS.md", "# Nested test rules\n")
    write("aaa/test_category.rb", "# category\n")
    write("test_category.rb", "# category\n")
    sh("git", "-C", @dir, "add", ".")

    result = run_tool(task: "category", story: "category", extra_options: ["--max-files", "1"])
    categories = result.fetch("candidates")
    assert_equal ["app/second.py"], categories.fetch("primary").map { |item| item.fetch("path") }
    assert_equal ["test_category.rb"], categories.fetch("tests").map { |item| item.fetch("path") }
  end

  def test_loads_root_and_nested_claude_instructions
    write("CLAUDE.md", "# Root rules\n")
    write("app/CLAUDE.md", "# Application rules\n")
    write("app/models/CLAUDE.md", "# Model rules\n")
    sh("git", "-C", @dir, "add", ".")

    paths = run_tool.fetch("candidates").fetch("instructions").map { |item| item.fetch("path") }
    assert_equal %w[AGENTS.md CLAUDE.md app/CLAUDE.md app/models/CLAUDE.md], paths
  end

  def test_accepts_safe_yaml_dates_in_task_story_and_sibling_inputs
    write("inputs/task.yaml", "id: T2\ncreated_at: 2026-09-05\nstory_clauses: [BEH-1]\ndependencies: [T1]\n")
    write("inputs/story.yaml", "updated_at: 2026-09-05T12:00:00Z\nnormalized_clauses:\n  - id: BEH-1\n    text: quartz\n")
    write("inputs/siblings.yaml", "created_at: 2026-09-05\ntasks:\n  - id: T1\n    title: quartz\n    required_behavior: [quartz]\n")

    result = run_tool(task: File.join(@dir, "inputs/task.yaml"), story: File.join(@dir, "inputs/story.yaml"), story_tasks: File.join(@dir, "inputs/siblings.yaml"))
    assert_equal ["BEH-1"], result.fetch("requirements").fetch("referenced_story_ids")
    assert_includes result.fetch("requirements").fetch("story"), "quartz"
  end

  def test_bounds_long_matching_lines_in_evidence_and_snippets
    write("app/minified.js", "a" * 2_000_000 + " quartz\n")
    sh("git", "-C", @dir, "add", ".")

    result = run_tool(task: "quartz", story: "quartz", extra_options: ["--max-matches-per-file", "1", "--max-snippet-lines", "1"])
    candidate = result.fetch("candidates").fetch("primary").find { |item| item.fetch("path") == "app/minified.js" }
    evidence = candidate.fetch("score_evidence").first.fetch("text")
    snippet = candidate.fetch("snippets").first.fetch("content")
    assert_operator evidence.length, :<=, 1000
    assert_operator snippet.chomp.length, :<=, 1000
    assert_includes evidence, "quartz"
    assert_includes snippet, "quartz"
    assert_includes snippet, "[truncated]"
    assert_operator JSON.generate(result).bytesize, :<, 12_000

    no_snippets = run_tool(task: "quartz", story: "quartz", extra_options: ["--max-snippet-lines", "0"])
    assert no_snippets.fetch("candidates").values.flatten.all? { |item| item.fetch("snippets").empty? }
  end

  def test_dependency_caps_preserve_primary_rank_and_import_order
    write("app/first.js", "const helper = require('./zulu1.js');\nimport other from './zulu2.js';\n" + "// category\n" * 20)
    write("app/second.js", "import helper from './alpha.js';\n// category\n")
    %w[zulu1 zulu2 alpha].each { |name| write("app/#{name}.js", "// Utility\n") }
    sh("git", "-C", @dir, "add", ".")

    result = run_tool(task: "category", story: "category", extra_options: ["--max-files", "2"])
    paths = result.fetch("candidates").fetch("dependencies").map { |item| item.fetch("path") }
    assert_equal %w[app/zulu1.js app/zulu2.js], paths
  end

  def test_long_unicode_lines_keep_original_character_positions
    write("app/minified.js", "İ" * 2000 + " quartz\n")
    sh("git", "-C", @dir, "add", ".")

    result = run_tool(task: "quartz", story: "quartz")
    candidate = result.fetch("candidates").fetch("primary").find { |item| item.fetch("path") == "app/minified.js" }
    evidence = candidate.fetch("score_evidence").first.fetch("text")
    snippet = candidate.fetch("snippets").first.fetch("content")
    assert_includes evidence, "quartz"
    assert_includes snippet, "quartz"
    assert_operator evidence.length, :<=, 1000
    assert_operator snippet.chomp.length, :<=, 1000
  end

  def test_prerequisite_only_matches_keep_their_evidence_and_location
    write("app/utility.rb", "# filler\n" * 120 + "# quartz\n")
    sh("git", "-C", @dir, "add", ".")
    task = { id: "T2", title: "category", dependencies: ["T1"] }.to_json
    siblings = { tasks: [{ id: "T1", title: "quartz", required_behavior: ["quartz"] }] }.to_json

    result = run_tool(task: task, story_tasks: siblings)
    candidate = result.fetch("candidates").fetch("prerequisites").find { |item| item.fetch("path") == "app/utility.rb" }
    refute_nil candidate
    assert_equal 121, candidate.fetch("score_evidence").first.fetch("line")
    assert_includes candidate.fetch("snippets").first.fetch("content"), "quartz"
    assert_operator candidate.fetch("snippets").first.fetch("start_line"), :>, 80
  end

  def test_javascript_comments_and_literals_do_not_consume_dependency_slots
    write("app/entry.js", <<~'JS')
      // import './fake.js';
      /* import './fake.js'; */
      const example = "import './fake.js';";
      const template = `import './fake.js';`;
      const pattern = /import '.\/fake.js'/;
      import {
        helper /* keep this comment */
      } from './real.js';
      // category
    JS
    write("app/fake.js", "// Utility\n")
    write("app/real.js", "// Utility\n")
    sh("git", "-C", @dir, "add", ".")

    result = run_tool(task: "category", story: "category", extra_options: ["--max-files", "1"])
    paths = result.fetch("candidates").fetch("dependencies").map { |item| item.fetch("path") }
    assert_equal ["app/real.js"], paths
  end

  def test_literal_javascript_dynamic_imports_are_dependencies
    write("app/entry.js", <<~JS)
      import('./quartz.js');
      import('./citrine.js', { with: { type: 'json' } });
      import(variable);
      import('./fake.js' + suffix);
      // category
    JS
    %w[quartz citrine fake].each { |name| write("app/#{name}.js", "// Utility\n") }
    sh("git", "-C", @dir, "add", ".")

    result = run_tool(task: "category", story: "category")
    paths = result.fetch("candidates").fetch("dependencies").map { |item| item.fetch("path") }
    assert_equal %w[app/quartz.js app/citrine.js], paths
  end

  def test_python_literal_examples_do_not_consume_dependency_slots
    write("app/entry.py", <<~'PY')
      """
      import app.fake
      from app import fake
      """
      example = '''
      import app.fake
      '''
      raw = r"""import app.fake"""
      plain = "import app.fake"
      # import app.fake
      from app import real
      # category
    PY
    write("app/fake.py", "# Utility\n")
    write("app/real.py", "# Utility\n")
    sh("git", "-C", @dir, "add", ".")

    result = run_tool(task: "category", story: "category", extra_options: ["--max-files", "1"])
    paths = result.fetch("candidates").fetch("dependencies").map { |item| item.fetch("path") }
    assert_equal ["app/real.py"], paths
  end

  def test_python_imports_support_semicolons_and_explicit_line_continuations
    write("app/entry.py", <<~'PY')
      import app.quartz; from app import citrine
      import app.amber, \
        app.opal
      # category
    PY
    %w[quartz citrine amber opal].each { |name| write("app/#{name}.py", "# Utility\n") }
    sh("git", "-C", @dir, "add", ".")

    result = run_tool(task: "category", story: "category")
    paths = result.fetch("candidates").fetch("dependencies").map { |item| item.fetch("path") }
    assert_equal %w[app/quartz.py app/citrine.py app/amber.py app/opal.py], paths
  end

  def test_go_comments_and_literals_do_not_create_dependencies
    write("go.mod", "module example.com/widget\n")
    write("cmd/app/main.go", <<~'GO')
      package main
      // import "example.com/widget/internal/fake"
      /* import "example.com/widget/internal/fake" */
      import "example.com/widget/internal/real"
      const example = `
      import "example.com/widget/internal/fake"
      `
      const quoted = "import \"example.com/widget/internal/fake\""
      // category
    GO
    %w[fake real].each { |name| write("internal/#{name}/helper.go", "package #{name}\n") }
    sh("git", "-C", @dir, "add", ".")

    [[], ["--max-files", "1"]].each do |limits|
      result = run_tool(task: "category", story: "category", extra_options: limits)
      paths = result.fetch("candidates").fetch("dependencies").map { |item| item.fetch("path") }
      assert_equal ["internal/real/helper.go"], paths
    end
  end

  def test_go_grouped_imports_support_aliases_and_raw_strings
    write("go.mod", "module example.com/widget\n")
    write("cmd/app/main.go", <<~'GO')
      package main
      import (
        alias "example.com/widget/internal/quartz"
        . `example.com/widget/internal/citrine`
      )
      // category
    GO
    %w[quartz citrine].each { |name| write("internal/#{name}/helper.go", "package #{name}\n") }
    sh("git", "-C", @dir, "add", ".")

    result = run_tool(task: "category", story: "category")
    paths = result.fetch("candidates").fetch("dependencies").map { |item| item.fetch("path") }
    assert_equal %w[internal/quartz/helper.go internal/citrine/helper.go], paths
  end

  private

  def run_tool(task: nil, story: nil, story_tasks: nil, extra_options: [])
    task ||= <<~TEXT
      title: Persist selected eBay category on drafts
      goal: Preserve the selected eBay category when a draft is saved and reopened.
      required_behavior: Save selected category as part of the draft and restore it when reopened.
    TEXT
    story ||= <<~TEXT
      As a user, I can select a category group and category from pre-populated menus.
      If I choose a category and come back to the draft later, that category is still selected.
    TEXT

    args = [
      TOOL,
      "--repo", @dir,
      "--task", task,
      "--story", story
    ]
    args.concat(["--story-tasks", story_tasks]) if story_tasks
    args.concat(extra_options)
    out, err, status = Open3.capture3(*args)
    assert status.success?, err
    JSON.parse(out)
  end

  def write(path, contents)
    full = File.join(@dir, path)
    FileUtils.mkdir_p(File.dirname(full))
    File.write(full, contents)
  end

  def sh(*args)
    out, err, status = Open3.capture3(*args)
    raise "command failed: #{args.join(' ')}\n#{out}\n#{err}" unless status.success?
    out
  end
end
