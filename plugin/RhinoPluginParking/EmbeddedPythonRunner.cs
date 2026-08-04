using System.Reflection;
using Rhino;

namespace RhinoPluginParking;

internal static class EmbeddedPythonRunner
{
    private const string ResourcePrefix = "RhinoPluginParking.Scripts.";

    public static bool Run(string scriptFileName)
    {
        try
        {
            // The scripts share a helper module, so every embedded script is extracted together.
            var scriptDirectory = ExtractAll();
            var scriptPath = Path.Combine(scriptDirectory, scriptFileName);
            if (!File.Exists(scriptPath))
                throw new InvalidOperationException($"Embedded script not found: {scriptFileName}");

            var escapedPath = scriptPath.Replace("\"", "\"\"");
            return RhinoApp.RunScript($"_-RunPythonScript \"{escapedPath}\"", false);
        }
        catch (Exception exception)
        {
            RhinoApp.WriteLine($"Parking plug-in error: {exception.Message}");
            return false;
        }
    }

    private static string ExtractAll()
    {
        var assembly = Assembly.GetExecutingAssembly();
        var version = assembly.GetName().Version?.ToString() ?? "current";
        var scriptDirectory = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "RhinoPluginParking",
            version);
        Directory.CreateDirectory(scriptDirectory);

        foreach (var resourceName in assembly.GetManifestResourceNames())
        {
            if (!resourceName.StartsWith(ResourcePrefix, StringComparison.Ordinal))
                continue;

            using var resource = assembly.GetManifestResourceStream(resourceName);
            if (resource is null)
                continue;

            using var memory = new MemoryStream();
            resource.CopyTo(memory);
            var embeddedBytes = memory.ToArray();

            var scriptPath = Path.Combine(scriptDirectory, resourceName.Substring(ResourcePrefix.Length));
            if (!File.Exists(scriptPath) || !File.ReadAllBytes(scriptPath).SequenceEqual(embeddedBytes))
                File.WriteAllBytes(scriptPath, embeddedBytes);
        }

        return scriptDirectory;
    }
}
