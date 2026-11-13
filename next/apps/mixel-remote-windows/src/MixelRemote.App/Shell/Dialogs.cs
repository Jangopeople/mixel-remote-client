using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Windows.ApplicationModel.DataTransfer;
using Windows.Storage.Pickers;

namespace MixelRemote.App.Shell;

internal static class Clipboard
{
    public static void Copy(string text)
    {
        var package = new DataPackage();
        package.SetText(text);
        Windows.ApplicationModel.DataTransfer.Clipboard.SetContent(package);
    }
}

/// <summary>File dialogs and the app's dialogs (confirmations and small forms).</summary>
internal static class Dialogs
{
    public static async Task<string?> OpenFile(nint owner)
    {
        var picker = new FileOpenPicker { ViewMode = PickerViewMode.List, SuggestedStartLocation = PickerLocationId.DocumentsLibrary };
        picker.FileTypeFilter.Add("*");
        WinRT.Interop.InitializeWithWindow.Initialize(picker, owner);
        var file = await picker.PickSingleFileAsync();
        return file?.Path;
    }

    public static async Task<string?> SaveFile(nint owner, string suggested)
    {
        var picker = new FileSavePicker { SuggestedStartLocation = PickerLocationId.DocumentsLibrary, SuggestedFileName = Path.GetFileNameWithoutExtension(suggested) };
        var extension = Path.GetExtension(suggested);
        picker.FileTypeChoices.Add(extension.Length > 1 ? extension[1..].ToUpperInvariant() : L("File"), [extension.Length > 1 ? extension : ".bin"]);
        WinRT.Interop.InitializeWithWindow.Initialize(picker, owner);
        var file = await picker.PickSaveFileAsync();
        return file?.Path;
    }

    private static ContentDialog Dialog(XamlRoot root, string title, object content, string primary, bool danger)
    {
        var dialog = new ContentDialog
        {
            XamlRoot = root,
            Title = title,
            Content = content,
            PrimaryButtonText = primary,
            CloseButtonText = L("Cancel"),
            DefaultButton = ContentDialogButton.Primary,
            RequestedTheme = (root.Content as FrameworkElement)?.ActualTheme ?? ElementTheme.Default,
        };
        if (!danger) dialog.PrimaryButtonStyle = Ui.Style("AccentButtonStyle");
        return dialog;
    }

    public static async Task<bool> Confirm(XamlRoot root, string title, string text, string action, bool danger = true)
    {
        var dialog = Dialog(root, title, Ui.Text(text), action, danger);
        return await dialog.ShowAsync() == ContentDialogResult.Primary;
    }

    /// <summary>
    /// A small form. <paramref name="submit"/> gets the field values and returns an error to show (the
    /// dialog stays open) or null (done).
    /// </summary>
    public static async Task Form(XamlRoot root, string title, string? text, IReadOnlyList<(string Label, string Value)> fields, string action,
        Func<string[], Task<string?>> submit, bool danger = false, UIElement? extra = null)
    {
        var boxes = fields.Select(field => new TextBox { Text = field.Value, MinWidth = 340 }).ToArray();
        var error = new TextBlock { Foreground = Ui.BrandBrush, TextWrapping = TextWrapping.Wrap, Visibility = Visibility.Collapsed };
        var stack = new StackPanel { Spacing = 12, MinWidth = 360 };
        if (text is not null) stack.Children.Add(Ui.Text(text, secondary: true));
        for (var index = 0; index < boxes.Length; index++) stack.Children.Add(Ui.Field(fields[index].Label, boxes[index]));
        if (extra is not null) stack.Children.Add(extra);
        stack.Children.Add(error);
        var dialog = Dialog(root, title, stack, action, danger);
        dialog.PrimaryButtonClick += async (sender, click) =>
        {
            var deferral = click.GetDeferral();
            dialog.IsPrimaryButtonEnabled = false;
            var problem = await submit([.. boxes.Select(box => box.Text)]);
            dialog.IsPrimaryButtonEnabled = true;
            if (problem is not null)
            {
                click.Cancel = true;
                error.Text = problem;
                error.Visibility = Visibility.Visible;
            }
            deferral.Complete();
        };
        await dialog.ShowAsync();
    }

    /// <summary>A dialog with custom content and only a close button.</summary>
    public static async Task Show(XamlRoot root, string title, UIElement content, string? primary = null, Func<Task<bool>>? onPrimary = null)
    {
        var dialog = new ContentDialog
        {
            XamlRoot = root,
            Title = title,
            Content = new ScrollViewer { Content = content, MaxHeight = 520 },
            CloseButtonText = L("Close"),
            RequestedTheme = (root.Content as FrameworkElement)?.ActualTheme ?? ElementTheme.Default,
        };
        if (primary is not null && onPrimary is not null)
        {
            dialog.PrimaryButtonText = primary;
            dialog.PrimaryButtonStyle = Ui.Style("AccentButtonStyle");
            dialog.DefaultButton = ContentDialogButton.Primary;
            dialog.PrimaryButtonClick += async (sender, click) =>
            {
                var deferral = click.GetDeferral();
                click.Cancel = !await onPrimary();
                deferral.Complete();
            };
        }
        await dialog.ShowAsync();
    }
}
