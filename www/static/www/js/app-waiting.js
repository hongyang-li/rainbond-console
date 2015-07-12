$(function() {
	setInterval("getGitCodeCheck()", 3000);
});

function getGitCodeCheck() {
	var tenantName = $('#tenantName').val();
	var service_name = $('#service_name').val();
	if (service_name != "" && service_name != undefined) {
		$.ajax({
			type : "GET",
			url : "/ajax/" + tenantName + "/" + service_name + "/check/",
			cache : false,
			success : function(msg) {
				var dataObj = msg;
				if (dataObj["status"] == "hidden"
						|| dataObj["status"] == "change") {
					window.location.href = "/app/" + tenantName + "/"
							+ service_name + "/app-language/"
				}
			},
			error : function() {
				// alert("系统异常");
			}
		})
	}
}